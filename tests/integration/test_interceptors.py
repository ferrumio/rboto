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
from rboto import OperationEvent, Session, operation_context
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
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with state.lock:
                state.requests += 1
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


@pytest.mark.asyncio
@service("dynamodb")
async def test_native_attempts_include_retry_metadata_and_resolved_region(peer: Peer) -> None:
    from rboto import DynamoDBClient

    events: list[OperationEvent] = []
    peer.failures = 1
    client = DynamoDBClient(endpoint_url=peer.endpoint, interceptors=[events.append])
    result = await client.list_tables()
    assert result.table_names == ["example"]
    start, end = events
    assert start.phase == "start" and start.region is None
    assert end.phase == "success" and end.region == "us-east-1"
    assert start.context is end.context
    assert end.context.service == "dynamodb" and end.context.operation == "ListTables"
    assert end.attempt_count == 2 and end.retry_count == 1
    assert end.status_code == 200 and end.request_id == "request-2"
    assert [attempt.status_code for attempt in end.attempts] == [503, 200]
    assert [attempt.request_id for attempt in end.attempts] == ["request-1", "request-2"]
    assert all(attempt.duration >= 0 for attempt in end.attempts)
    assert end.duration is not None
    assert sum(attempt.duration for attempt in end.attempts) <= end.duration


@pytest.mark.asyncio
@service("dynamodb")
@pytest.mark.parametrize("exhausted", [False, True])
async def test_native_failures_preserve_modeled_exceptions(peer: Peer, exhausted: bool) -> None:
    from rboto import DynamoDBClient
    from rboto_dynamodb.exceptions import ResourceNotFoundError

    events: list[OperationEvent] = []
    peer.status = 400
    peer.body = b'{"__type":"ResourceNotFoundException","message":"private table name"}'
    peer.failures = 10 if exhausted else 0
    client = DynamoDBClient(
        region="us-east-1", endpoint_url=peer.endpoint, interceptors=[events.append]
    )
    with pytest.raises(ClientError if exhausted else ResourceNotFoundError):
        await client.describe_table(table_name="private")
    assert [event.phase for event in events] == ["start", "failure"]
    end = events[-1]
    assert end.attempt_count == (3 if exhausted else 1)
    assert end.request_id == f"request-{end.attempt_count}"
    assert end.status_code == (503 if exhausted else 400)
    assert end.error_code == ("ServiceUnavailable" if exhausted else "ResourceNotFoundException")
    assert "private" not in repr(events)


@pytest.mark.asyncio
@service("dynamodb")
async def test_transport_failure_produces_one_failure_event(peer: Peer) -> None:
    from rboto import DynamoDBClient

    # Hold a non-listening socket so another process cannot take this address.
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{closed.getsockname()[1]}"
        events: list[OperationEvent] = []
        client = DynamoDBClient(
            region="us-east-1", endpoint_url=endpoint, interceptors=[events.append]
        )
        with pytest.raises(ClientError):
            await client.list_tables()
    assert [event.phase for event in events] == ["start", "failure"]
    assert events[-1].attempt_count == 3
    assert events[-1].status_code is None and events[-1].request_id is None


@pytest.mark.asyncio
@service("dynamodb")
async def test_cancelled_native_call_does_not_deliver_late_success(peer: Peer) -> None:
    from rboto import DynamoDBClient

    peer.header_gate = threading.Event()
    events: list[OperationEvent] = []
    client = DynamoDBClient(
        region="us-east-1", endpoint_url=peer.endpoint, interceptors=[events.append]
    )
    task = asyncio.create_task(client.list_tables())
    assert await asyncio.to_thread(peer.received.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [event.phase for event in events] == ["start", "cancelled"]
    cancelled = events[-1]
    peer.header_gate.set()
    # A subsequent real operation proves that the client remains usable while
    # any late native completion can no longer affect the first event.
    assert (await client.list_tables()).table_names == ["example"]
    assert [event.phase for event in events] == ["start", "cancelled", "start", "success"]
    assert cancelled.attempt_count is None
    assert cancelled.context.operation_id != events[-1].context.operation_id


@pytest.mark.asyncio
@service("s3")
async def test_stream_headers_complete_operation_without_consuming_body(peer: Peer) -> None:
    from rboto import S3Client

    peer.body = b"private streamed contents"
    peer.content_type = "application/octet-stream"
    peer.body_gate = threading.Event()
    events: list[OperationEvent] = []
    client = S3Client(region="us-east-1", endpoint_url=peer.endpoint, interceptors=[events.append])
    result = await asyncio.wait_for(client.get_object(bucket="example", key="private"), 5)
    assert [event.phase for event in events] == ["start", "success"]
    assert events[-1].attempt_count == 1
    assert "private" not in repr(events)
    peer.body_gate.set()
    assert await result.body.read() == peer.body
    assert await result.body.read() == b""
    assert len(events) == 2


@pytest.mark.asyncio
@service("s3", "dynamodb")
async def test_concurrent_service_wheels_inherit_session_without_context_leaks(peer: Peer) -> None:
    from rboto import DynamoDBClient, S3Client, s3

    inherited: list[OperationEvent] = []
    overridden: list[OperationEvent] = []
    session = Session(
        region="us-east-1", endpoint_url=peer.endpoint, interceptors=(inherited.append,)
    )
    dynamo = DynamoDBClient(session=session)
    storage = S3Client(session=session)
    silent = s3(session=session, interceptors=())
    other = S3Client(session=session, interceptors=[overridden.append])

    async def run(index: int) -> None:
        with operation_context(correlation_id=str(index)):
            if index % 2:
                await dynamo.list_tables()
            else:
                await storage.head_object(bucket="example", key=str(index))

    await asyncio.gather(*(run(i) for i in range(20)))
    await silent.head_object(bucket="example", key="silent")
    await other.head_object(bucket="example", key="other")
    assert len(inherited) == 40 and len(overridden) == 2
    terminal = [event for event in inherited if event.phase == "success"]
    assert {event.context.correlation_id for event in terminal} == {str(i) for i in range(20)}
    assert {event.context.service for event in terminal} == {"s3", "dynamodb"}
    assert len({event.context.operation_id for event in terminal}) == 20
    assert len({event.request_id for event in terminal}) == 20


@service("dynamodb")
def test_one_native_client_can_be_observed_across_event_loops(peer: Peer) -> None:
    from rboto import DynamoDBClient

    events: list[OperationEvent] = []
    client = DynamoDBClient(
        region="us-east-1", endpoint_url=peer.endpoint, interceptors=[events.append]
    )
    for _ in range(3):
        assert asyncio.run(client.list_tables()).table_names == ["example"]
    assert [event.phase for event in events] == ["start", "success"] * 3
    assert len({event.context.operation_id for event in events}) == 3


@pytest.mark.asyncio
@service("dynamodb")
async def test_observer_errors_do_not_replace_native_results(peer: Peer) -> None:
    from rboto import DynamoDBClient

    errors: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))

    async def broken(event: OperationEvent) -> None:
        await asyncio.sleep(0)
        raise RuntimeError("observer failed")

    try:
        client = DynamoDBClient(
            region="us-east-1", endpoint_url=peer.endpoint, interceptors=[broken]
        )
        assert (await client.list_tables()).table_names == ["example"]
        assert len(errors) == 2
    finally:
        loop.set_exception_handler(previous)
