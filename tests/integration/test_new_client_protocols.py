"""Exercise generated conversions through the real SDK and a local HTTP peer."""

import asyncio
import base64
import json
import os
import struct
import threading
import zlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and name not in SELECTED), reason="service not selected"
    )


def event_frame(event: str, payload: dict[str, object], *, exception: bool = False) -> bytes:
    headers = bytearray()
    for name, value in {
        ":message-type": "exception" if exception else "event",
        ":exception-type" if exception else ":event-type": event,
        ":content-type": "application/json",
    }.items():
        key, text = name.encode(), value.encode()
        headers += bytes([len(key)]) + key + b"\x07" + struct.pack(">H", len(text)) + text
    body = json.dumps(payload).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    message = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


@dataclass
class Peer:
    endpoint: str = ""
    body: bytes = b"{}"
    status: int = 200
    content_type: str = "application/json"
    duplex: bool = False
    requests: list[tuple[str, bytes]] = field(default_factory=list)
    request_received: threading.Event = field(default_factory=threading.Event)
    response_gate: threading.Event | None = None


@pytest.fixture
def peer(monkeypatch: pytest.MonkeyPatch) -> Iterator[Peer]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    config = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def read_body(self) -> bytes:
            if self.headers.get("Transfer-Encoding") != "chunked":
                return self.rfile.read(int(self.headers.get("Content-Length", "0")))
            chunks = []
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                size = int(line.split(b";")[0], 16)
                if not size:
                    while self.rfile.readline() not in {b"\r\n", b""}:
                        pass
                    break
                chunks.append(self.rfile.read(size))
                assert self.rfile.read(2) == b"\r\n"
            return b"".join(chunks)

        def send_chunk(self, body: bytes) -> None:
            self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
            self.wfile.flush()

        def do_POST(self) -> None:
            if config.duplex:
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.amazon.eventstream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.send_chunk(
                    event_frame("chunk", {"bytes": base64.b64encode(b"ready").decode()})
                )
                config.requests.append((self.path, self.read_body()))
                self.send_chunk(event_frame("chunk", {"bytes": base64.b64encode(b"done").decode()}))
                self.send_chunk(b"")
                return
            config.requests.append((self.path, self.read_body()))
            gate = config.response_gate
            config.request_received.set()
            if gate is not None:
                assert gate.wait(timeout=5)
            self.send_response(config.status)
            self.send_header("Content-Type", config.content_type)
            self.send_header("Content-Length", str(len(config.body)))
            self.end_headers()
            try:
                self.wfile.write(config.body)
            except (BrokenPipeError, ConnectionResetError):
                # A canceled request may close the connection before the peer replies.
                pass

        do_GET = do_POST
        do_PUT = do_POST

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    config.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield config
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
@service("dynamodb")
async def test_native_outputs_keep_owned_values_and_fresh_mappings(peer: Peer) -> None:
    from rboto import dynamodb

    peer.body = json.dumps(
        {
            "Item": {"pk": {"S": "example"}, "nested": {"L": [{"N": "1"}, {"BOOL": True}]}},
            "ConsumedCapacity": {"TableName": "example", "CapacityUnits": 1.0},
        }
    ).encode()
    client = dynamodb(region="us-east-1", endpoint_url=peer.endpoint)
    outputs = await asyncio.gather(
        *(client.get_item(table_name="example", key={"pk": {"s": "example"}}) for _ in range(8))
    )
    del client
    for output in outputs:
        assert type(output).__name__ == "GetItemOutput"
        expected = {"pk": {"s": "example"}, "nested": {"l": [{"n": "1"}, {"bool": True}]}}
        assert output.item == expected
        item = output.item
        assert item is not None
        item.clear()
        assert output.item == expected
        assert output.to_dict()["item"] == expected
        assert output.consumed_capacity is not None
        assert output.consumed_capacity.capacity_units == 1.0
        with pytest.raises(AttributeError):
            output.item = {}


@pytest.mark.asyncio
@service("dynamodb")
async def test_input_containers_preserve_unsized_iterables(peer: Peer) -> None:
    from rboto import dynamodb

    class Attributes:
        def __iter__(self) -> Iterator[str]:
            return iter(["pk", "nested"])

        def __len__(self) -> int:
            raise AssertionError("input conversion must not require an iterable length")

    peer.body = b'{"Item":{"pk":{"S":"example"}}}'
    client = dynamodb(region="us-east-1", endpoint_url=peer.endpoint)
    for attributes in (["pk", "nested"], Attributes()):
        output = await client._native.get_item(
            {
                "table_name": "example",
                "key": {"pk": {"s": "example"}},
                "attributes_to_get": attributes,
                "expression_attribute_names": {f"#field{i}": f"field{i}" for i in range(64)},
            }
        )
        assert output.item == {"pk": {"s": "example"}}
        request = json.loads(peer.requests[-1][1])
        assert request["AttributesToGet"] == ["pk", "nested"]
        assert len(request["ExpressionAttributeNames"]) == 64


@pytest.mark.asyncio
@service("dynamodb")
async def test_native_request_cancellation_keeps_client_usable(peer: Peer) -> None:
    from rboto import dynamodb

    peer.body = b'{"Item":{"pk":{"S":"example"}}}'
    gate = threading.Event()
    peer.response_gate = gate
    client = dynamodb(region="us-east-1", endpoint_url=peer.endpoint)
    pending = asyncio.create_task(
        client.get_item(table_name="example", key={"pk": {"s": "example"}})
    )
    try:
        async with asyncio.timeout(10):
            assert await asyncio.to_thread(peer.request_received.wait, 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            peer.response_gate = None
            gate.set()
            output = await client.get_item(table_name="example", key={"pk": {"s": "example"}})
            assert output.item == {"pk": {"s": "example"}}
    finally:
        gate.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@service("dynamodb")
async def test_native_request_preserves_modeled_errors(peer: Peer) -> None:
    from rboto import dynamodb
    from rboto_dynamodb.exceptions import ResourceNotFoundError

    peer.status = 400
    peer.body = b'{"__type":"ResourceNotFoundException","message":"missing table"}'
    client = dynamodb(region="us-east-1", endpoint_url=peer.endpoint)
    with pytest.raises(ResourceNotFoundError, match="missing table") as caught:
        await client.get_item(table_name="missing", key={"pk": {"s": "example"}})
    assert caught.value.operation_name == "GetItem"


@pytest.mark.asyncio
@service("bedrock-runtime")
async def test_documents_unions_and_native_outputs(peer: Peer) -> None:
    from rboto import bedrock_runtime

    document = {"nested": [None, True, 42, -7, 0.25, {"value": "hello"}]}
    peer.body = json.dumps(
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "Hello"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
            "metrics": {"latencyMs": 1},
            "additionalModelResponseFields": document,
        }
    ).encode()
    client = bedrock_runtime(region="us-east-1", endpoint_url=peer.endpoint)
    result = await client.converse(
        model_id="example",
        messages=[{"role": "user", "content": [{"text": "hello"}]}],
        additional_model_request_fields=document,
        inference_config={"temperature": 0.5},
    )
    assert result.additional_model_response_fields == document
    assert result.to_dict()["additional_model_response_fields"] == document
    assert result.usage.input_tokens == 2
    with pytest.raises(AttributeError):
        result.usage.input_tokens = 99
    request = json.loads(peer.requests[0][1])
    assert request["additionalModelRequestFields"] == document
    assert request["messages"][0]["content"] == [{"text": "hello"}]
    assert request["inferenceConfig"]["temperature"] == 0.5


@pytest.mark.asyncio
@service("bedrock-runtime")
async def test_invalid_documents_and_multivariant_unions_fail_before_io(peer: Peer) -> None:
    from rboto import bedrock_runtime

    client = bedrock_runtime(region="us-east-1", endpoint_url=peer.endpoint)
    with pytest.raises(ValueError, match="exactly one"):
        await client.converse(
            model_id="example",
            messages=[{"role": "user", "content": [{"text": "x", "tool_use": {}}]}],
        )
    with pytest.raises(ValueError, match="finite"):
        await client.converse(
            model_id="example", additional_model_request_fields={"x": float("nan")}
        )
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    with pytest.raises(ValueError, match="nesting"):
        await client.converse(model_id="example", additional_model_request_fields=recursive)
    assert not peer.requests


@pytest.mark.asyncio
@service("bedrock-runtime")
async def test_input_and_output_event_streams_advance_concurrently(peer: Peer) -> None:
    from rboto import bedrock_runtime
    from rboto_bedrock_runtime.types import InvokeModelWithBidirectionalStreamInput

    peer.duplex = True
    released = asyncio.Event()

    async def events() -> AsyncIterator[InvokeModelWithBidirectionalStreamInput]:
        yield {"chunk": {"bytes": b"first"}}
        await released.wait()
        yield {"chunk": {"bytes": b"second"}}

    client = bedrock_runtime(region="us-east-1", endpoint_url=peer.endpoint)
    async with asyncio.timeout(15):
        response = await client.invoke_model_with_bidirectional_stream(
            model_id="example", body=events()
        )
        stream = response["body"]
        assert await anext(stream) == {"chunk": {"bytes": b"ready"}}
        waiting = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        released.set()
        assert [event async for event in stream] == [{"chunk": {"bytes": b"done"}}]
    assert base64.b64encode(b"first") in peer.requests[0][1]
    assert base64.b64encode(b"second") in peer.requests[0][1]


@pytest.mark.asyncio
@service("bedrock-runtime")
async def test_modeled_event_stream_errors_are_catchable(peer: Peer) -> None:
    from rboto import bedrock_runtime
    from rboto_bedrock_runtime.exceptions import ValidationError

    peer.content_type = "application/vnd.amazon.eventstream"
    peer.body = event_frame("validationException", {"message": "invalid stream"}, exception=True)
    client = bedrock_runtime(region="us-east-1", endpoint_url=peer.endpoint)
    response = await client.invoke_model_with_response_stream(model_id="example", body=b"{}")
    with pytest.raises(ValidationError, match="invalid stream"):
        await anext(response["body"])
    with pytest.raises(StopAsyncIteration):
        await anext(response["body"])


@pytest.mark.asyncio
@service("qbusiness")
async def test_recursive_inputs_and_sparse_native_outputs(peer: Peer) -> None:
    from rboto import qbusiness

    peer.body = json.dumps(
        {
            "systemMessage": "answer",
            "sourceAttributions": [None, {"title": "Example", "snippet": "source"}],
        }
    ).encode()
    client = qbusiness(region="us-east-1", endpoint_url=peer.endpoint)
    result = await client.chat_sync(
        application_id="example",
        user_message="hello",
        attribute_filter={
            "not_filter": {
                "not_filter": {
                    "equals_to": {
                        "name": "kind",
                        "value": {"string_value": "test"},
                    }
                }
            }
        },
    )
    assert result.source_attributions is not None
    assert result.source_attributions[0] is None
    source = result.source_attributions[1]
    assert source is not None and source.title == "Example"
    assert json.loads(peer.requests[0][1])["attributeFilter"]["notFilter"]["notFilter"] == {
        "equalsTo": {"name": "kind", "value": {"stringValue": "test"}},
    }


@pytest.mark.asyncio
@service("secrets-manager")
async def test_new_service_modeled_error(peer: Peer) -> None:
    from rboto import secrets_manager
    from rboto_secrets_manager.exceptions import ResourceNotFoundError

    peer.status = 400
    peer.body = b'{"__type":"ResourceNotFoundException","Message":"missing secret"}'
    client = secrets_manager(region="us-east-1", endpoint_url=peer.endpoint)
    with pytest.raises(ResourceNotFoundError, match="missing secret") as caught:
        await client.get_secret_value(secret_id="missing")
    assert caught.value.operation_name == "GetSecretValue"


@pytest.mark.asyncio
@service("polly")
async def test_byte_streams_keep_independent_ownership(peer: Peer) -> None:
    from rboto import polly

    peer.body = b"audio response"
    peer.content_type = "audio/mpeg"
    client = polly(region="us-east-1", endpoint_url=peer.endpoint)
    first, second = await asyncio.gather(
        client.synthesize_speech(output_format="mp3", text="one", voice_id="Joanna"),
        client.synthesize_speech(output_format="mp3", text="two", voice_id="Joanna"),
    )
    assert first.audio_stream is not None and second.audio_stream is not None
    assert first.audio_stream is first.audio_stream
    assert first.audio_stream is not second.audio_stream
    assert await first.audio_stream.read() == b"audio response"
    assert await first.audio_stream.read() == b""
    assert await second.audio_stream.read() == b"audio response"


@pytest.mark.asyncio
@service("bedrock-agentcore-control")
async def test_unit_union_variants_and_rust_keyword_fields(peer: Peer) -> None:
    from rboto import bedrock_agentcore_control

    client = bedrock_agentcore_control(region="us-east-1", endpoint_url=peer.endpoint)
    await client.create_harness(
        harness_name="example",
        execution_role_arn="arn:aws:iam::123456789012:role/example",
        tools=[
            {
                "type": "agentcore_gateway",
                "config": {
                    "agent_core_gateway": {
                        "gateway_arn": (
                            "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/example"
                        ),
                        "outbound_auth": {"aws_iam": None},
                    },
                },
            }
        ],
    )
    tool = json.loads(peer.requests[0][1])["tools"][0]
    assert tool["type"] == "agentcore_gateway"
    assert tool["config"]["agentCoreGateway"]["outboundAuth"] == {"awsIam": {}}
