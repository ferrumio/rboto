"""Exercise observation across the compiled bridge with deterministic local I/O."""

import asyncio
import io
import json
import logging
import os
import socket
import struct
import threading
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from rboto import (
    Next,
    OperationRequest,
    OperationResponse,
    Session,
    configure_logging,
    get_logger,
    operation_context,
)
from rboto.exceptions import ClientError
from rboto.logging import OperationLogFields, _LoggingInterceptor

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(*names: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and not set(names) <= SELECTED), reason="services not selected"
    )


def encoded_event(message_type: str, name: str, payload: bytes) -> bytes:
    headers = bytearray()
    for key, value in {
        ":message-type": message_type,
        ":exception-type" if message_type == "exception" else ":event-type": name,
        ":content-type": "application/json",
    }.items():
        key_bytes, value_bytes = key.encode(), value.encode()
        headers += bytes([len(key_bytes)]) + key_bytes
        headers += b"\x07" + struct.pack(">H", len(value_bytes)) + value_bytes
    prelude = struct.pack(">II", 16 + len(headers) + len(payload), len(headers))
    frame = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + payload
    return frame + struct.pack(">I", zlib.crc32(frame))


@pytest.mark.asyncio
@service("dynamodb")
@pytest.mark.parametrize("observed", [False, True])
async def test_modeled_details_and_metadata_are_typed_and_isolated(
    peer: "Peer", observed: bool,
) -> None:
    from rboto import DynamoDBClient
    from rboto_dynamodb.exceptions import ConditionalCheckFailedError

    peer.status = 400
    peer.body = json.dumps({
        "__type": "ConditionalCheckFailedException", "message": "condition failed",
        "Item": {"pk": {"S": "private item"}, "nested": {"M": {"ok": {"BOOL": True}}}},
        "unknownSecret": "discard this",
    }).encode()
    recorder = Recorder()
    async with DynamoDBClient(
        endpoint_url=peer.endpoint, interceptors=[recorder] if observed else [],
    ) as client:
        with pytest.raises(ConditionalCheckFailedError) as caught:
            await client.put_item(table_name="example", item={"pk": {"s": "key"}})
    error = caught.value
    assert error.details["item"] == {
        "pk": {"s": "private item"}, "nested": {"m": {"ok": {"bool": True}}},
    }
    assert "unknownSecret" not in error.details
    assert "private item" not in str(error) + repr(error.response)
    assert error.response_metadata == {
        "HTTPStatusCode": 400, "RequestId": "request-1", "HostId": None, "RetryAttempts": 0,
    }
    if observed:
        metadata = recorder.requests[0].metadata
        assert metadata.status_code == error.response_metadata["HTTPStatusCode"]
        assert metadata.retry_count == error.response_metadata["RetryAttempts"]


@pytest.mark.asyncio
@service("dynamodb")
async def test_connection_interrupted_during_buffered_operation_response(peer: "Peer") -> None:
    from rboto import ClientConfig, DynamoDBClient
    from rboto.exceptions import ConnectionError

    peer.truncate = True
    async with DynamoDBClient(
        endpoint_url=peer.endpoint, config=ClientConfig(max_attempts=1),
    ) as client:
        with pytest.raises(ConnectionError) as caught:
            await client.list_tables()
    assert caught.value.operation_name == "ListTables"
    assert caught.value.response_metadata["HTTPStatusCode"] == 200
    assert caught.value.request_id == "request-1"


@pytest.mark.asyncio
@service("bedrock-runtime")
@pytest.mark.parametrize("failure", ["modeled", "crc", "json", "truncated", "partial-frame"])
async def test_event_stream_errors_retain_initial_response_context(
    peer: "Peer", failure: str,
) -> None:
    from rboto import BedrockRuntimeClient
    from rboto.exceptions import ConnectionError, ResponseError
    from rboto_bedrock_runtime import ResponseStreamReceiver
    from rboto_bedrock_runtime.exceptions import ModelStreamError

    peer.content_type = "application/vnd.amazon.eventstream"
    peer.body = encoded_event("event", "chunk", b'{"bytes":"Zmlyc3Q="}')
    if failure == "modeled":
        peer.body += encoded_event("exception", "modelStreamErrorException", json.dumps({
            "message": "model stream failed", "originalStatusCode": 503,
            "originalMessage": "private model response", "unknownSecret": "discard this",
        }).encode())
    elif failure == "crc":
        peer.body += encoded_event("event", "chunk", b"private undecodable response")[:-1] + b"\xff"
    elif failure == "json":
        peer.body += encoded_event("event", "chunk", b"private undecodable response")
    elif failure == "partial-frame":
        peer.body += encoded_event("event", "chunk", b"private undecodable response")[:10]
    else:
        peer.truncate = True
    async with BedrockRuntimeClient(endpoint_url=peer.endpoint) as client:
        output = await client.invoke_model_with_response_stream(model_id="example", body=b"{}")
    receiver = output["body"]
    assert isinstance(receiver, ResponseStreamReceiver)
    assert await anext(receiver) == {"chunk": {"bytes": b"first"}}
    expected = ModelStreamError if failure == "modeled" else (
        ConnectionError if failure == "truncated" else ResponseError
    )
    with pytest.raises(expected) as caught:
        await anext(receiver)
    error = caught.value
    assert error.operation_name == "InvokeModelWithResponseStream"
    assert error.request_id == "request-1"
    assert error.response_metadata["HTTPStatusCode"] == 200
    assert error.response_metadata["RetryAttempts"] == 0
    assert "private" not in str(error) + repr(error.response)
    if isinstance(error, ModelStreamError):
        assert error.details["original_status_code"] == 503
        assert error.details["original_message"] == "private model response"
        assert "unknownSecret" not in error.details
    with pytest.raises(StopAsyncIteration):
        await anext(receiver)
    await receiver.aclose()


@dataclass
class Peer:
    endpoint: str = ""
    status: int = 200
    failures: int = 0
    body: bytes = b'{"TableNames":["example"]}'
    content_type: str = "application/x-amz-json-1.0"
    requests: int = 0
    payloads: list[bytes] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    received: threading.Event = field(default_factory=threading.Event)
    header_gate: threading.Event | None = None
    body_gate: threading.Event | None = None
    truncate: bool = False


@pytest.fixture
def peer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Peer]:
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name)
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_CONFIG_FILE": str(tmp_path / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "credentials"),
        "AWS_MAX_ATTEMPTS": "3",
        "AWS_RETRY_MODE": "standard",
        "AWS_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    state = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with state.lock:
                state.requests += 1
                state.payloads.append(payload)
                state.paths.append(self.path)
                number = state.requests
            state.received.set()
            if state.header_gate is not None:
                assert state.header_gate.wait(10)
            status = 503 if number <= state.failures else state.status
            body = (
                b'{"__type":"ServiceUnavailable","message":"retry"}'
                if status == 503
                else state.body
            )
            self.send_response(status)
            self.send_header("Content-Type", state.content_type)
            self.send_header("Content-Length", str(len(body) + (10 if state.truncate else 0)))
            self.send_header("x-amzn-requestid", f"request-{number}")
            self.end_headers()
            if state.body_gate is not None:
                assert state.body_gate.wait(10)
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                    if state.truncate:
                        self.close_connection = True
                except (BrokenPipeError, ConnectionResetError):
                    pass

        do_GET = do_POST
        do_HEAD = do_POST

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        for gate in (state.header_gate, state.body_gate):
            if gate is not None:
                gate.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


class Recorder:
    def __init__(self) -> None:
        self.requests: list[OperationRequest] = []

    async def __call__(self, request: OperationRequest, call_next: Next) -> OperationResponse:
        self.requests.append(request)
        return await call_next(request)


@pytest.mark.asyncio
@service("dynamodb")
async def test_request_mutation_and_native_retry_metadata(peer: Peer) -> None:
    import json

    from rboto import DynamoDBClient

    recorder = Recorder()
    order: list[str] = []
    peer.failures = 1

    async def limit(request: OperationRequest, call_next: Next) -> OperationResponse:
        order.append("before")
        request.parameters["limit"] = 5
        response = await call_next(request)
        order.append("after")
        return response

    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[limit, recorder])
    result = await client.list_tables()
    assert result.table_names == ["example"]
    assert order == ["before", "after"]
    assert all(json.loads(body)["Limit"] == 5 for body in peer.payloads)
    request = recorder.requests[0]
    metadata = request.metadata
    assert request.context.operation == "ListTables"
    assert metadata.region == "us-east-1" and metadata.dispatched
    assert metadata.attempt_count == 2 and metadata.retry_count == 1
    assert metadata.status_code == 200 and metadata.request_id == "request-2"
    assert [attempt.status_code for attempt in metadata.attempts] == [503, 200]
    assert [attempt.request_id for attempt in metadata.attempts] == ["request-1", "request-2"]


@pytest.mark.asyncio
@service("dynamodb")
@pytest.mark.parametrize("exhausted", [False, True])
async def test_native_failures_preserve_modeled_exceptions(peer: Peer, exhausted: bool) -> None:
    from rboto import DynamoDBClient
    from rboto_dynamodb.exceptions import ResourceNotFoundError

    recorder = Recorder()
    peer.status = 400
    peer.body = b'{"__type":"ResourceNotFoundException","message":"private table name"}'
    peer.failures = 10 if exhausted else 0
    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[recorder])
    with pytest.raises(ClientError if exhausted else ResourceNotFoundError):
        await client.describe_table(table_name="private")
    assert len(recorder.requests) == 1
    metadata = recorder.requests[0].metadata
    assert metadata.attempt_count == (3 if exhausted else 1)
    assert metadata.request_id == f"request-{metadata.attempt_count}"
    assert metadata.status_code == (503 if exhausted else 400)
    assert metadata.error_code == (
        "ServiceUnavailable" if exhausted else "ResourceNotFoundException"
    )
    assert "private" not in repr(metadata)


@pytest.mark.asyncio
@service("dynamodb")
async def test_policy_error_blocks_network_request(peer: Peer) -> None:
    from rboto import DynamoDBClient

    async def deny(request: OperationRequest, call_next: Next) -> OperationResponse:
        raise PermissionError("blocked by application")

    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[deny])
    with pytest.raises(PermissionError, match="blocked by application"):
        await client.list_tables()
    assert peer.requests == 0


@pytest.mark.asyncio
@service("dynamodb")
@pytest.mark.parametrize("forward", [False, True])
async def test_native_response_can_be_cached_or_replaced(peer: Peer, forward: bool) -> None:
    from rboto import DynamoDBClient

    cached = await DynamoDBClient(endpoint_url=peer.endpoint).list_tables()
    peer.body = b'{"TableNames":["different"]}'
    requests: list[OperationRequest] = []

    async def cache(request: OperationRequest, call_next: Next) -> OperationResponse:
        requests.append(request)
        if forward:
            response = await call_next(request)
            assert response.output is not cached
            response.output = cached
            return response
        return OperationResponse(cached)

    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[cache])
    result = await client.list_tables()
    assert result is cached
    assert result.table_names == ["example"]
    assert peer.requests == (2 if forward else 1)
    assert requests[0].metadata.dispatched is forward


@pytest.mark.asyncio
@service("dynamodb")
async def test_middleware_recovers_from_native_error(peer: Peer) -> None:
    from rboto import DynamoDBClient

    fallback = await DynamoDBClient(endpoint_url=peer.endpoint).list_tables()
    peer.status = 400
    peer.body = b'{"__type":"AccessDeniedException","message":"denied"}'

    async def recover(request: OperationRequest, call_next: Next) -> OperationResponse:
        try:
            return await call_next(request)
        except ClientError:
            assert request.metadata.status_code == 400
            return OperationResponse(fallback)

    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[recover])
    assert await client.list_tables() is fallback
    assert peer.requests == 2


@pytest.mark.asyncio
@service("dynamodb")
async def test_invalid_replacement_is_rejected(peer: Peer) -> None:
    from rboto import DynamoDBClient

    async def wrong(request: OperationRequest, call_next: Next) -> OperationResponse:
        return OperationResponse({"table_names": ["wrong type"]})

    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[wrong])
    with pytest.raises(TypeError, match="ListTablesOutput"):
        await client.list_tables()
    assert peer.requests == 0


@pytest.mark.asyncio
@service("dynamodb")
async def test_transport_failure_metadata(peer: Peer) -> None:
    from rboto import DynamoDBClient

    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{closed.getsockname()[1]}"
        recorder = Recorder()
        client = DynamoDBClient(endpoint_url=endpoint, interceptors=[recorder])
        with pytest.raises(ClientError):
            await client.list_tables()
    metadata = recorder.requests[0].metadata
    assert metadata.attempt_count == 3
    assert metadata.status_code is None and metadata.request_id is None


@pytest.mark.asyncio
@service("dynamodb")
async def test_cancelled_native_call_unwinds_once_and_keeps_snapshot(peer: Peer) -> None:
    from rboto import DynamoDBClient

    peer.header_gate = threading.Event()
    recorder = Recorder()
    finished: list[str] = []

    async def cleanup(request: OperationRequest, call_next: Next) -> OperationResponse:
        try:
            return await call_next(request)
        finally:
            finished.append(request.context.operation_id)

    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[recorder, cleanup])
    task = asyncio.create_task(client.list_tables())
    assert await asyncio.to_thread(peer.received.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancelled = recorder.requests[0].metadata
    assert cancelled.error_type == "CancelledError"
    peer.header_gate.set()
    assert (await client.list_tables()).table_names == ["example"]
    assert len(finished) == 2 and len(set(finished)) == 2
    assert len(recorder.requests) == 2
    assert recorder.requests[0].metadata is cancelled
    assert cancelled.attempt_count is None


@pytest.mark.asyncio
@service("s3")
async def test_stream_is_not_consumed_by_the_chain(peer: Peer) -> None:
    from rboto import S3Client

    peer.body = b"private streamed contents"
    peer.content_type = "application/octet-stream"
    peer.body_gate = threading.Event()
    recorder = Recorder()
    client = S3Client(endpoint_url=peer.endpoint, interceptors=[recorder])
    result = await asyncio.wait_for(client.get_object(bucket="example", key="private"), 5)
    assert len(recorder.requests) == 1
    metadata = recorder.requests[0].metadata
    assert metadata.attempt_count == 1 and metadata.status_code == 200
    assert "private" not in repr(metadata)
    peer.body_gate.set()
    assert await result.body.read() == peer.body
    assert await result.body.read() == b""
    assert recorder.requests[0].metadata is metadata


@pytest.mark.asyncio
@service("s3", "dynamodb")
async def test_concurrent_service_wheels_and_session_configuration(peer: Peer) -> None:
    from rboto import DynamoDBClient, S3Client, s3

    inherited = Recorder()
    overridden = Recorder()
    session = Session(region="us-east-1", endpoint_url=peer.endpoint, interceptors=(inherited,))
    dynamo = DynamoDBClient(session=session)
    storage = S3Client(session=session)
    silent = s3(session=session, interceptors=())
    other = S3Client(session=session, interceptors=[overridden])

    async def run(index: int) -> None:
        with operation_context(correlation_id=str(index)):
            if index % 2:
                await dynamo.list_tables()
            else:
                await storage.head_object(bucket="example", key=str(index))

    await asyncio.gather(*(run(i) for i in range(20)))
    await silent.head_object(bucket="example", key="silent")
    await other.head_object(bucket="example", key="other")
    assert len(inherited.requests) == 20 and len(overridden.requests) == 1
    assert {r.context.correlation_id for r in inherited.requests} == {str(i) for i in range(20)}
    assert {r.context.service for r in inherited.requests} == {"s3", "dynamodb"}
    assert len({r.context.operation_id for r in inherited.requests}) == 20
    assert len({r.metadata.request_id for r in inherited.requests}) == 20


@service("dynamodb")
def test_native_client_middleware_across_event_loops(peer: Peer) -> None:
    from rboto import DynamoDBClient

    recorder = Recorder()
    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[recorder])
    for _ in range(3):
        assert asyncio.run(client.list_tables()).table_names == ["example"]
    assert len(recorder.requests) == 3
    assert len({r.context.operation_id for r in recorder.requests}) == 3


def log_fields(caplog: pytest.LogCaptureFixture) -> list[OperationLogFields]:
    return [r.__dict__["rboto"] for r in caplog.records if r.name.startswith("rboto.")]


@pytest.mark.asyncio
@service("dynamodb")
@pytest.mark.parametrize("outcome", ["success", "retry", "modeled", "exhausted"])
async def test_logging_native_outcomes(
    peer: Peer,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
) -> None:
    from rboto import DynamoDBClient
    from rboto_dynamodb.exceptions import ResourceNotFoundError

    caplog.set_level(logging.DEBUG, logger="rboto")
    secret = "private-input-output-exception-CANARY"
    peer.failures = 10 if outcome == "exhausted" else 1 if outcome == "retry" else 0
    if outcome == "modeled":
        peer.status = 400
        peer.body = f'{{"__type":"ResourceNotFoundException","message":"{secret}"}}'.encode()
    else:
        peer.body = f'{{"TableNames":["{secret}"]}}'.encode()
    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[_LoggingInterceptor()])
    if outcome in {"modeled", "exhausted"}:
        with pytest.raises(ResourceNotFoundError if outcome == "modeled" else ClientError):
            await client.describe_table(table_name=secret)
    else:
        assert (await client.list_tables()).table_names == [secret]
    rows = log_fields(caplog)
    [completion] = [row for row in rows if row["event"] == "operation.completed"]
    count = 3 if outcome == "exhausted" else 2 if outcome == "retry" else 1
    assert completion["attempt_count"] == count
    assert completion["retry_count"] == count - 1
    assert completion["request_id"] == f"request-{count}"
    assert completion["outcome"] == ("error" if outcome in {"modeled", "exhausted"} else "success")
    assert completion["error_code"] == (
        "ResourceNotFoundException"
        if outcome == "modeled"
        else "ServiceUnavailable"
        if outcome == "exhausted"
        else None
    )
    assert len(rows) == (2 if count > 1 else 1)
    if count > 1:
        assert len(rows[1]["attempts"]) == count
    assert secret not in caplog.text
    assert all(r.exc_info is None for r in caplog.records if r.name.startswith("rboto."))


@pytest.mark.asyncio
@service("dynamodb")
async def test_logging_transport_failure(peer: Peer, caplog: pytest.LogCaptureFixture) -> None:
    from rboto import DynamoDBClient

    caplog.set_level(logging.INFO, logger="rboto")
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{closed.getsockname()[1]}"
        client = DynamoDBClient(endpoint_url=endpoint, interceptors=[_LoggingInterceptor()])
        with pytest.raises(ClientError):
            await client.list_tables()
    [row] = log_fields(caplog)
    assert row["outcome"] == "error" and row["attempt_count"] == 3
    assert row["status_code"] is None and row["request_id"] is None
    assert endpoint not in caplog.text


@pytest.mark.asyncio
@service("dynamodb")
async def test_logging_cancelled_native_call(
    peer: Peer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from rboto import DynamoDBClient

    caplog.set_level(logging.INFO, logger="rboto")
    peer.header_gate = threading.Event()
    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[_LoggingInterceptor()])
    task = asyncio.create_task(client.list_tables())
    assert await asyncio.to_thread(peer.received.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [cancelled] = log_fields(caplog)
    assert cancelled["outcome"] == "cancelled" and cancelled["attempt_count"] is None
    snapshot = cancelled.copy()
    peer.header_gate.set()
    await client.list_tables()
    assert len(log_fields(caplog)) == 2
    assert cancelled == snapshot


@pytest.mark.asyncio
@service("s3")
async def test_logging_returns_before_body_and_does_not_consume_stream(
    peer: Peer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from rboto import S3Client

    caplog.set_level(logging.DEBUG, logger="rboto")
    peer.body = b"stream-CANARY"
    peer.content_type = "application/octet-stream"
    peer.body_gate = threading.Event()
    client = S3Client(endpoint_url=peer.endpoint, interceptors=[_LoggingInterceptor()])
    result = await asyncio.wait_for(client.get_object(bucket="example", key="key-CANARY"), 5)
    [row] = log_fields(caplog)
    assert row["outcome"] == "success" and row["status_code"] == 200
    assert "CANARY" not in caplog.text
    peer.body_gate.set()
    assert await result.body.read() == peer.body
    assert await result.body.read() == b""
    assert len(log_fields(caplog)) == 1


@service("s3", "dynamodb")
def test_logging_concurrent_clients_and_reused_event_loops(
    peer: Peer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from rboto import DynamoDBClient, S3Client

    caplog.set_level(logging.INFO, logger="rboto")
    session = Session(endpoint_url=peer.endpoint, interceptors=(_LoggingInterceptor(),))
    dynamo, storage = DynamoDBClient(session=session), S3Client(session=session)

    async def run(start: int) -> None:
        async def call(number: int) -> None:
            with operation_context(correlation_id=str(number)):
                if number % 2:
                    await dynamo.list_tables()
                else:
                    await storage.head_object(bucket="example", key=str(number))

        await asyncio.gather(*(call(n) for n in range(start, start + 10)))

    asyncio.run(run(0))
    asyncio.run(run(10))
    rows = log_fields(caplog)
    assert len(rows) == len({r["operation_id"] for r in rows}) == 20
    assert {r["correlation_id"] for r in rows} == {str(n) for n in range(20)}
    assert {r["service"] for r in rows} == {"s3", "dynamodb"}
    assert len({r["request_id"] for r in rows}) == 20



def json_records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@service("s3", "dynamodb")
def test_global_logging_existing_clients_and_service_controls(
    peer: Peer,
    json_logging: io.StringIO,
) -> None:
    from rboto import DynamoDBClient, S3Client

    dynamo = DynamoDBClient(endpoint_url=peer.endpoint)
    storage = S3Client(endpoint_url=peer.endpoint)
    assert dynamo.logger_name == "rboto.dynamodb" and storage.logger_name == "rboto.s3"
    asyncio.run(dynamo.list_tables())
    assert json_records(json_logging) == []
    configure_logging(stream=json_logging)

    async def concurrent() -> None:
        async def call(index: int) -> None:
            with operation_context(correlation_id=str(index)):
                if index % 2:
                    await dynamo.list_tables()
                else:
                    await storage.head_object(bucket="example", key="private-CANARY")

        await asyncio.gather(*(call(n) for n in range(10)))

    asyncio.run(concurrent())
    records = json_records(json_logging)
    assert len(records) == 10
    assert {record["logger"] for record in records} == {"rboto.s3", "rboto.dynamodb"}
    assert {record["correlation_id"] for record in records} == {str(n) for n in range(10)}
    assert all(record["level"] == "INFO" and "timestamp" in record for record in records)
    assert "CANARY" not in json_logging.getvalue()

    get_logger("s3").disabled = True
    asyncio.run(concurrent())
    assert len(json_records(json_logging)) == 15
    configure_logging(service_levels={"dynamodb": "OFF"}, stream=json_logging)
    asyncio.run(concurrent())
    assert len(json_records(json_logging)) == 20
    configure_logging(level="OFF", stream=json_logging)
    asyncio.run(concurrent())
    assert len(json_records(json_logging)) == 20


@pytest.mark.asyncio
@service("dynamodb")
async def test_global_json_retries_and_failure(
    peer: Peer,
    json_logging: io.StringIO,
) -> None:
    from rboto import DynamoDBClient
    from rboto_dynamodb.exceptions import ResourceNotFoundError

    configure_logging(level="DEBUG", stream=json_logging)
    client = DynamoDBClient(endpoint_url=peer.endpoint)
    peer.failures = 1
    await client.list_tables()
    completion, retry = json_records(json_logging)
    assert completion["retry_count"] == 1
    assert retry["event"] == "operation.retry" and retry["level"] == "DEBUG"
    peer.status = 400
    peer.body = b'{"__type":"ResourceNotFoundException","message":"private-CANARY"}'
    with pytest.raises(ResourceNotFoundError):
        await client.describe_table(table_name="private-CANARY")
    error = json_records(json_logging)[-1]
    assert error["level"] == "ERROR" and error["error_code"] == "ResourceNotFoundException"
    assert "CANARY" not in json_logging.getvalue()


@pytest.mark.asyncio
@service("s3")
async def test_global_json_does_not_read_streams(peer: Peer, json_logging: io.StringIO) -> None:
    from rboto import S3Client

    configure_logging(stream=json_logging)
    peer.body = b"private-stream-CANARY"
    peer.content_type = "application/octet-stream"
    peer.body_gate = threading.Event()
    client = S3Client(endpoint_url=peer.endpoint)
    output = await asyncio.wait_for(client.get_object(bucket="example", key="private-CANARY"), 5)
    assert len(json_records(json_logging)) == 1
    assert "CANARY" not in json_logging.getvalue()
    peer.body_gate.set()
    assert await output.body.read() == peer.body
    assert await output.body.read() == b""
    assert len(json_records(json_logging)) == 1
