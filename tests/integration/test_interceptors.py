"""Exercise observation across the compiled bridge with deterministic local I/O."""

import asyncio
import os
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from rboto import Next, OperationRequest, OperationResponse, Session, operation_context
from rboto.exceptions import ClientError

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(*names: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and not set(names) <= SELECTED), reason="services not selected"
    )


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
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-amzn-requestid", f"request-{number}")
            self.end_headers()
            if state.body_gate is not None:
                assert state.body_gate.wait(10)
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
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
