"""Waiters through compiled clients and deterministic HTTP peers."""

from __future__ import annotations

import asyncio
import io
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from rboto import ClientConfig, Next, OperationRequest, OperationResponse

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and name not in SELECTED), reason=f"{name} not selected",
    )


@dataclass
class Peer:
    endpoint: str = ""
    replies: list[tuple[int, bytes]] = field(default_factory=list[tuple[int, bytes]])
    requests: list[tuple[str, bytes, str | None]] = field(
        default_factory=list[tuple[str, bytes, str | None]],
    )
    gate: threading.Event | None = None
    received: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def peer(monkeypatch: pytest.MonkeyPatch) -> Iterator[Peer]:
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    state = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def respond(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.requests.append((self.path, body, self.headers.get("Authorization")))
            state.received.set()
            if state.gate is not None:
                state.gate.wait(5)
            status, response = state.replies.pop(0) if state.replies else (500, b"{}")
            self.send_response(status)
            self.send_header("Content-Type", "application/x-amz-json-1.0")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("x-amzn-requestid", f"request-{len(state.requests)}")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        do_POST = respond
        do_GET = respond
        do_HEAD = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        if state.gate is not None:
            state.gate.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def config(*, attempts: int = 1) -> ClientConfig:
    from rboto import ClientConfig
    return ClientConfig(
        access_key_id="testing", secret_access_key="testing",
        max_attempts=attempts, operation_timeout=3,
    )


@pytest.mark.asyncio
@service("dynamodb")
async def test_public_operation_preserves_native_retries_interceptors_and_logging(
    peer: Peer, json_logging: io.StringIO,
) -> None:
    from rboto import DynamoDBClient, WaiterConfig, configure_logging
    from rboto_dynamodb.types import DescribeTableOutput

    peer.replies = [
        (500, b'{"__type":"InternalServerError","message":"retry"}'),
        (200, b'{"Table":{"TableName":"changed","TableStatus":"CREATING"}}'),
        (200, b'{"Table":{"TableName":"changed","TableStatus":"ACTIVE"}}'),
    ]
    recorded: list[OperationRequest] = []

    async def intercept(request: OperationRequest, call_next: Next) -> OperationResponse:
        request.parameters["table_name"] = "changed"
        recorded.append(request)
        return await call_next(request)

    configure_logging(level="DEBUG", stream=json_logging)
    client = DynamoDBClient(
        region="us-east-1", endpoint_url=peer.endpoint, config=config(attempts=2),
        interceptors=[intercept],
    )
    output = await client.waiters.table_exists(
        table_name="original", waiter_config=WaiterConfig(delay=0, max_attempts=2, timeout=5),
    )
    assert isinstance(output, DescribeTableOutput)
    assert output.table is not None and output.table.table_status == "ACTIVE"
    assert output.table.table_name == "changed"
    assert len(recorded) == 2 and len(peer.requests) == 3
    assert [request.metadata.attempt_count for request in recorded] == [2, 1]
    assert all(request.context.operation == "DescribeTable" for request in recorded)
    assert all(json.loads(body)["TableName"] == "changed" and authorization
               for _, body, authorization in peer.requests)
    records = [cast(dict[str, object], json.loads(line))
               for line in json_logging.getvalue().splitlines()]
    assert any(record["event"] == "operation.retry" for record in records)
    assert len([record for record in records if record["event"] != "operation.retry"]) == 2


@pytest.mark.asyncio
@service("dynamodb")
async def test_native_modeled_errors_can_retry_succeed_or_propagate(peer: Peer) -> None:
    from rboto import DynamoDBClient, WaiterConfig
    from rboto_dynamodb.exceptions import ResourceNotFoundError

    missing = (400, b'{"__type":"ResourceNotFoundException","message":"gone"}')
    peer.replies = [
        missing, (200, b'{"Table":{"TableStatus":"ACTIVE"}}'), missing, missing,
    ]
    client = DynamoDBClient(region="us-east-1", endpoint_url=peer.endpoint, config=config())
    await client.waiters.table_exists(table_name="example", waiter_config=WaiterConfig(delay=0))
    await client.waiters.table_not_exists(table_name="example")
    # The same native error remains a terminal operation failure on another waiter.
    with pytest.raises(ResourceNotFoundError):
        await client.waiters.export_completed(export_arn="arn:example")
    assert len(peer.requests) == 4


@pytest.mark.asyncio
@service("lambda")
async def test_nested_lambda_state_and_terminal_failure(peer: Peer) -> None:
    from rboto import LambdaClient, WaiterConfig
    from rboto.waiters import WaiterFailedError
    from rboto_lambda.types import GetFunctionOutput

    peer.replies = [
        (200, b'{"Configuration":{"State":"Pending"}}'),
        (200, b'{"Configuration":{"State":"Active"}}'),
        (200, b'{"Configuration":{"State":"Failed"}}'),
    ]
    client = LambdaClient(region="us-east-1", endpoint_url=peer.endpoint, config=config())
    result = await client.waiters.function_active_v2(
        function_name="example", qualifier="1", waiter_config=WaiterConfig(delay=0),
    )
    assert isinstance(result, GetFunctionOutput)
    assert result.configuration is not None and result.configuration.state == "Active"
    assert all("Qualifier=1" in path for path, _, _ in peer.requests)
    with pytest.raises(WaiterFailedError) as caught:
        await client.waiters.function_active_v2(function_name="example")
    assert caught.value.attempts == 1 and len(peer.requests) == 3


@pytest.mark.asyncio
@service("s3")
async def test_s3_head_success_and_modeled_not_found(peer: Peer) -> None:
    from rboto import S3Client, WaiterConfig
    from rboto_s3.types import HeadBucketOutput, HeadObjectOutput

    peer.replies = [(404, b""), (200, b""), (404, b""), (200, b""), (404, b"")]
    client = S3Client(region="us-east-1", endpoint_url=peer.endpoint, config=config())
    assert isinstance(await client.waiters.bucket_exists(
        bucket="example", waiter_config=WaiterConfig(delay=0),
    ), HeadBucketOutput)
    await client.waiters.bucket_not_exists(bucket="example")
    assert isinstance(await client.waiters.object_exists(
        bucket="example", key="key",
    ), HeadObjectOutput)
    await client.waiters.object_not_exists(bucket="example", key="key")
    assert len(peer.requests) == 5


@pytest.mark.asyncio
@service("dynamodb")
@pytest.mark.parametrize("during_request", [True, False])
async def test_native_deadline_stops_polling(peer: Peer, during_request: bool) -> None:
    from rboto import DynamoDBClient, WaiterConfig
    from rboto.waiters import WaiterTimeoutError

    peer.replies = [(200, b'{"Table":{"TableStatus":"CREATING"}}')]
    if during_request:
        peer.gate = threading.Event()
    client = DynamoDBClient(region="us-east-1", endpoint_url=peer.endpoint, config=config())
    task = asyncio.create_task(client.waiters.table_exists(
        table_name="example", waiter_config=WaiterConfig(delay=3600, timeout=0.5),
    ))
    async with asyncio.timeout(3):
        with pytest.raises(WaiterTimeoutError) as caught:
            await task
    assert caught.value.reason == "deadline"
    assert peer.received.is_set()
    if peer.gate is not None:
        peer.gate.set()
    await asyncio.sleep(0.05)
    assert len(peer.requests) == 1


@pytest.mark.asyncio
@service("lambda")
async def test_cancellation_of_pending_native_operation_stops_waiter(peer: Peer) -> None:
    from rboto import LambdaClient, WaiterConfig

    peer.gate = threading.Event()
    peer.replies = [(200, b'{"State":"Pending"}')]
    client = LambdaClient(region="us-east-1", endpoint_url=peer.endpoint, config=config())
    task = asyncio.create_task(client.waiters.function_active(
        function_name="example", waiter_config=WaiterConfig(delay=0),
    ))
    async with asyncio.timeout(3):
        while not peer.received.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    peer.gate.set()
    await asyncio.sleep(0.05)
    assert len(peer.requests) == 1
