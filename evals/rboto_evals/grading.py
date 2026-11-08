"""Private behavioral cases. Run model-written code only in the grading container."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import importlib.util
import inspect
import json
import os
import random
import re
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from .catalog import Task, task_named


@dataclass
class Reply:
    body: bytes = b"{}"
    status: int = 200
    content_type: str = "application/json"
    chunked: bool = False


Request = tuple[str, str, dict[str, str], bytes]


class Peer:
    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.errors: list[str] = []
        self.respond: Callable[[Request], Reply] = lambda request: Reply()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request = (
                    self.command,
                    self.path,
                    {key.lower(): value for key, value in self.headers.items()},
                    body,
                )
                owner.requests.append(request)
                try:
                    reply = owner.respond(request)
                except Exception:
                    owner.errors.append("request_contract")
                    reply = Reply(status=400)
                self.send_response(reply.status)
                self.send_header("Content-Type", reply.content_type)
                if reply.chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                try:
                    if reply.chunked:
                        for offset in range(0, len(reply.body), 4093):
                            chunk = reply.body[offset : offset + 4093]
                            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.write(b"0\r\n\r\n")
                    else:
                        self.wfile.write(reply.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_POST = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


async def invoke(module: ModuleType, *arguments: object) -> object:
    function = cast(Callable[..., Awaitable[object]], getattr(module, "run"))
    assert inspect.iscoroutinefunction(function), "run_must_be_async"
    return await function(*arguments)


def json_reply(value: object) -> Reply:
    return Reply(json.dumps(value).encode())


async def projection(module: ModuleType, peer: Peer, values: list[str]) -> None:
    from pydantic import BaseModel
    from rboto_s3.types import BucketOutput

    model = getattr(module, "BucketSummary")
    assert issubclass(model, BaseModel), "pydantic_model_required"
    xml = (
        "<ListAllMyBucketsResult><Buckets>"
        + "".join(f"<Bucket><Name>{escape(name)}</Name></Bucket>" for name in values)
        + "<Bucket/></Buckets></ListAllMyBucketsResult>"
    )
    peer.respond = lambda request: Reply(xml.encode(), content_type="application/xml")
    with patch.object(model, "model_validate", wraps=model.model_validate) as validate:
        result = await invoke(module, peer.endpoint)
        assert isinstance(result, list), "list_required"
        assert all(isinstance(item, BaseModel) for item in result), "pydantic_projection"
        assert [item.model_dump() for item in result] == [{"name": value} for value in values], (
            "native_output_or_nullability"
        )
        assert len(validate.call_args_list) == len(values), "attribute_validation_required"
        assert all(isinstance(call.args[0], BucketOutput) for call in validate.call_args_list), (
            "project_native_attributes"
        )
    peer.respond = lambda request: Reply(
        b"<ListAllMyBucketsResult/>",
        content_type="application/xml",
    )
    assert await invoke(module, peer.endpoint) == [], "absent_collection"
    assert len(peer.requests) == 2, "one_list_request_per_invocation"


async def pages(module: ModuleType, peer: Peer, values: list[str]) -> None:
    table, partition, first, last = values
    cursors = [None, {"pk": {"S": "cursor-a"}}, {"pk": {"S": "cursor-b"}}]
    replies = [
        {"Items": [{"label": {"S": first}}], "LastEvaluatedKey": cursors[1]},
        {"LastEvaluatedKey": cursors[2]},
        {"Items": [{}, {"label": {"N": "7"}}, {"label": {"S": last}}]},
    ]
    index = 0

    def respond(request: Request) -> Reply:
        nonlocal index
        method, _, headers, raw = request
        assert method == "POST" and headers["x-amz-target"].endswith(".Query")
        body = json.loads(raw)
        assert body["TableName"] == table
        assert body["KeyConditionExpression"] == "#pk = :pk"
        assert body["ExpressionAttributeNames"] == {"#pk": "pk"}
        assert body["ExpressionAttributeValues"] == {":pk": {"S": partition}}
        assert index < 3 and body.get("ExclusiveStartKey") == cursors[index]
        result = json_reply(replies[index])
        index += 1
        return result

    peer.respond = respond
    assert await invoke(module, peer.endpoint, table, partition) == [
        first,
        last,
    ], "all_pages_and_attribute_variants"
    assert index == 3, "empty_page_is_not_terminal"
    peer.respond = lambda request: json_reply({})
    assert await invoke(module, peer.endpoint, table, partition) == [], "absent_items"


async def acknowledgements(module: ModuleType, peer: Peer, values: list[str]) -> None:
    url = peer.endpoint + "/123456789012/" + values[0]
    handled: list[str] = []
    deleted: list[str] = []
    messages: list[dict[str, str]] = [
        {"Body": values[1], "ReceiptHandle": "receipt-a"},
        {"Body": "", "ReceiptHandle": "receipt-b"},
        {"Body": values[2]},
        {"ReceiptHandle": "receipt-c"},
    ]

    def respond(request: Request) -> Reply:
        _, _, headers, raw = request
        body = json.loads(raw)
        assert body["QueueUrl"] == url
        if headers["x-amz-target"].endswith(".ReceiveMessage"):
            assert body["MaxNumberOfMessages"] == 10
            return json_reply({"Messages": messages})
        assert headers["x-amz-target"].endswith(".DeleteMessage")
        receipt = body["ReceiptHandle"]
        position = len(deleted)
        assert receipt == ("receipt-a", "receipt-b")[position]
        assert len(handled) == position + 1, "ack_before_handler"
        deleted.append(receipt)
        return json_reply({})

    peer.respond = respond

    async def handler(body: str) -> None:
        await asyncio.sleep(0)
        handled.append(body)

    assert await invoke(module, peer.endpoint, url, handler) == 2, "ack_count"
    assert handled == [values[1], ""] and deleted == [
        "receipt-a",
        "receipt-b",
    ], "message_fields_and_acknowledgement"

    class HandlerFailure(Exception):
        pass

    failure = HandlerFailure()
    handled.clear()
    deleted.clear()
    messages[:] = [
        {"Body": values[1], "ReceiptHandle": "receipt-a"},
        {"Body": values[2], "ReceiptHandle": "receipt-b"},
        {"Body": values[3], "ReceiptHandle": "receipt-c"},
    ]

    async def failing(body: str) -> None:
        if body == values[2]:
            raise failure
        await handler(body)

    try:
        await invoke(module, peer.endpoint, url, failing)
    except HandlerFailure as error:
        assert error is failure, "preserve_handler_exception"
    else:
        raise AssertionError("propagate_handler_failure")
    assert handled == [values[1]] and deleted == ["receipt-a"], "do_not_ack_failed_message"
    peer.respond = lambda request: json_reply({})
    assert await invoke(module, peer.endpoint, url, handler) == 0, "absent_messages"


async def streaming(module: ModuleType, peer: Peer, values: list[str]) -> None:
    from rboto import S3Client
    from rboto.exceptions import ClientError, StreamClosedError
    from rboto_s3.types import GetObjectOutput

    payload = (values[0] + "\0" + values[1]).encode() * 8192
    outputs: list[GetObjectOutput] = []
    original = S3Client.get_object

    async def capture(self: S3Client, *, bucket: str, key: str) -> GetObjectOutput:
        output = await original(self, bucket=bucket, key=key)
        outputs.append(output)
        return output

    peer.respond = lambda request: Reply(
        payload,
        content_type="application/octet-stream",
        chunked=True,
    )
    with patch.object(S3Client, "get_object", capture):
        result = await invoke(module, peer.endpoint, values[2], values[3])
        assert result == hashlib.sha256(payload).hexdigest(), "complete_stream_digest"
        assert urlsplit(peer.requests[-1][1]).path == f"/{values[2]}/{values[3]}", (
            "object_selection"
        )
        assert len(outputs) == 1, "one_download"
        try:
            await outputs[0].body.__aenter__()
        except StreamClosedError:
            pass
        else:
            raise AssertionError("close_response_stream")
    peer.respond = lambda request: Reply(
        b"<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>",
        404,
        "application/xml",
    )
    assert await invoke(module, peer.endpoint, values[2], values[3]) is None, "modeled_missing"
    peer.respond = lambda request: Reply(
        b"<Error><Code>AccessDenied</Code><Message>denied</Message></Error>",
        403,
        "application/xml",
    )
    try:
        await invoke(module, peer.endpoint, values[2], values[3])
    except ClientError as error:
        assert error.error_code == "AccessDenied", "preserve_other_service_errors"
    else:
        raise AssertionError("do_not_swallow_other_errors")


async def inference(module: ModuleType, peer: Peer, values: list[str]) -> None:
    model, question, first, last = values
    outputs: list[dict[str, object] | None] = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": first},
                    {"toolUse": {"toolUseId": "tool-1", "name": "example", "input": {}}},
                    {"text": last},
                ],
            }
        },
        None,
        {"futureVariant": {}},
    ]
    index = 0

    def respond(request: Request) -> Reply:
        nonlocal index
        method, path, _, raw = request
        assert method == "POST" and path == f"/model/{model}/converse"
        body = json.loads(raw)
        assert body["messages"] == [{"role": "user", "content": [{"text": question}]}]
        assert body["inferenceConfig"] == {"maxTokens": 32, "temperature": 0.0}
        output = outputs[index]
        index += 1
        reply: dict[str, object] = {"stopReason": "end_turn"}
        if output is not None:
            reply["output"] = output
        return json_reply(reply)

    peer.respond = respond
    for expected in (first + last, "", ""):
        assert await invoke(module, peer.endpoint, model, question) == expected, (
            "nested_input_or_output_variant"
        )
    assert index == 3, "one_converse_per_invocation"


CASES = {
    "s3_projection": projection,
    "dynamodb_pages": pages,
    "sqs_ack": acknowledgements,
    "s3_stream": streaming,
    "bedrock_input": inference,
}
CLIENTS = {
    "s3": "S3Client",
    "dynamodb": "DynamoDBClient",
    "sqs": "SQSClient",
    "bedrock-runtime": "BedrockRuntimeClient",
}


async def behavior(task: Task, solution: Path, seed: int) -> dict[str, object]:
    import rboto
    from rboto.exceptions import ClientClosedError

    peer = Peer()
    clients: list[object] = []
    client_class = getattr(rboto, CLIENTS[task.service])
    original = client_class.__init__

    def capture(self: object, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)
        clients.append(self)

    try:
        spec = importlib.util.spec_from_file_location("candidate", solution)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["candidate"] = module
        with patch.object(client_class, "__init__", capture):
            spec.loader.exec_module(module)
            rng = random.Random(seed)
            values = ["case-" + rng.randbytes(10).hex() for _ in range(4)]
            async with asyncio.timeout(15):
                await CASES[task.name](module, peer, values)
        assert not peer.errors, "request_contract"
        assert clients, "expected_client"
        for client in clients:
            try:
                getattr(client, "_native")._ensure_open()
            except ClientClosedError:
                continue
            raise AssertionError("close_client")
        return {"ok": True, "reason": None, "requests": len(peer.requests)}
    except Exception as error:
        reason = str(error) if type(error) is AssertionError else type(error).__name__
        return {
            "ok": False,
            "reason": peer.errors[0] if peer.errors else reason[:160],
            "requests": len(peer.requests),
        }
    finally:
        for client in clients:
            await getattr(client, "aclose")()
        peer.close()
        sys.modules.pop("candidate", None)


def source_policy(task: Task, code: str) -> list[str]:
    tree = ast.parse(code)
    errors = []
    if re.search(r"#\s*(type:\s*ignore|mypy:)", code):
        errors.append("type_check_suppression")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {"Any", "getattr", "eval", "exec"}:
            errors.append("typing_or_introspection_escape")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                errors.append("private_api")
            if node.attr == "to_dict" and task.name in {"s3_projection", "bedrock_input"}:
                errors.append("native_projection_required")
            if node.attr in {"read", "collect"} and task.name == "s3_stream":
                errors.append("async_iteration_required")
    return sorted(set(errors))


def grade(task: Task, directory: Path, seed: int, version: str) -> dict[str, object]:
    """Trusted grader entry; candidate execution requires external isolation."""
    try:
        submission = json.loads((directory / "submission.json").read_text())
        code = submission["code"]
        dependencies = submission["dependencies"]
        if (
            not isinstance(code, str)
            or not isinstance(dependencies, list)
            or not all(isinstance(item, str) for item in dependencies)
        ):
            return {"ok": False, "reason": "invalid_submission"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"ok": False, "reason": "invalid_submission"}
    if sorted(dependencies) != sorted(task.dependencies(version)):
        return {"ok": False, "reason": "dependency_selection"}
    solution = directory / "solution.py"
    if solution.read_text() != code:
        return {"ok": False, "reason": "source_mismatch"}
    try:
        policy = source_policy(task, code)
    except SyntaxError:
        return {"ok": False, "reason": "invalid_syntax"}
    typed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--no-incremental",
            "--cache-dir",
            "/tmp/rboto-eval-mypy",
            "--follow-imports=silent",
            "--show-error-codes",
            str(solution),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    diagnostics = (typed.stdout + typed.stderr).replace(str(directory), "<project>")[:12000]
    codes = re.findall(r"\[([a-z][a-z-]+)\]", diagnostics)
    result: dict[str, object] = {
        "ok": False,
        "policy_errors": policy,
        "typing": {
            "ok": typed.returncode == 0,
            "diagnostics": diagnostics,
            "error_codes": {code: codes.count(code) for code in sorted(set(codes))},
        },
    }
    # Do not run code that opts out of the contract or does not type-check.
    if policy or typed.returncode:
        result["reason"] = "source_policy" if policy else "typing"
        return result
    with open(os.devnull, "w") as output, contextlib.redirect_stdout(output):
        checked = asyncio.run(behavior(task, solution, seed))
    result["behavior"] = checked
    result["ok"] = checked["ok"]
    result["reason"] = checked["reason"]
    return result


def main() -> None:
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_MAX_ATTEMPTS="1",
    )
    result = grade(task_named(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    print(json.dumps(result))


if __name__ == "__main__":
    main()
