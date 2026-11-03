"""Telemetry through compiled clients, without contacting remote services."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from rboto import (
    ClientConfig,
    Next,
    OperationRequest,
    OperationResponse,
    Session,
    _observability,
    configure_logging,
)
from rboto.integrations.opentelemetry import OpenTelemetry, configure_opentelemetry

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and name not in SELECTED), reason=f"{name} not selected",
    )


@dataclass
class Peer:
    endpoint: str = ""
    replies: list[tuple[int, bytes]] = field(default_factory=list[tuple[int, bytes]])
    headers: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    body_gate: threading.Event | None = None
    request_gate: threading.Event | None = None
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
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def respond(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with lock:
                state.headers.append({name.lower(): value for name, value in self.headers.items()})
                request_id = f"request-{len(state.headers)}"
                status, body = state.replies.pop(0) if state.replies else (500, b"{}")
            state.received.set()
            if state.request_gate is not None:
                state.request_gate.wait(5)
            try:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/x-amz-json-1.0")
                self.send_header("x-amzn-requestid", request_id)
                self.send_header("x-amz-request-id", request_id)
                if status == 404:
                    self.send_header("x-amzn-errortype", "ResourceNotFoundException")
                self.end_headers()
                self.wfile.flush()
                if state.body_gate is not None:
                    state.body_gate.wait(5)
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        for gate in (state.body_gate, state.request_gate):
            if gate is not None:
                gate.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
def telemetry() -> Iterator[tuple[TracerProvider, InMemorySpanExporter, InMemoryMetricReader]]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    previous = _observability._telemetry
    configure_opentelemetry(
        tracer_provider=provider, meter_provider=meter, record_attempts=True,
    )
    try:
        yield provider, exporter, reader
    finally:
        _observability._telemetry = previous
        provider.shutdown()
        meter.shutdown()


def config(*, attempts: int = 1) -> ClientConfig:
    return ClientConfig(
        access_key_id="testing", secret_access_key="testing",
        max_attempts=attempts, operation_timeout=3,
    )


@service("dynamodb")
@pytest.mark.asyncio
async def test_native_retries_typed_output_metrics_and_json_correlation(
    peer: Peer, telemetry: tuple[TracerProvider, InMemorySpanExporter, InMemoryMetricReader],
    json_logging: io.StringIO,
) -> None:
    from rboto import DynamoDBClient

    provider, exporter, reader = telemetry
    configure_logging(stream=json_logging)
    peer.replies = [
        (500, b'{"__type":"InternalServerError","message":"private-error"}'),
        (200, b'{"TableNames":["private-table"]}'),
    ]
    seen: list[object] = []

    async def middleware(request: OperationRequest, call_next: Next) -> OperationResponse:
        response = await call_next(request)
        seen.append(response.output)
        return response

    client = DynamoDBClient(
        region="us-east-1", endpoint_url=peer.endpoint, config=config(attempts=2),
        interceptors=(middleware,),
    )
    with provider.get_tracer("application").start_as_current_span("parent"):
        output = await client.list_tables()
    assert output is seen[0]
    assert output.table_names == ["private-table"]
    spans = [span for span in exporter.get_finished_spans() if span.name != "parent"]
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "DynamoDB.ListTables"
    assert span.attributes is not None and span.context is not None
    assert span.attributes["rboto.native.retry_count"] == 1
    assert span.attributes["aws.request_id"] == "request-2"
    assert len(span.events) == 2
    record = json.loads(json_logging.getvalue())
    assert record["trace_id"] == f"{span.context.trace_id:032x}"
    assert record["span_id"] == f"{span.context.span_id:016x}"
    assert "private-" not in span.to_json() + json_logging.getvalue()
    for headers in peer.headers:
        assert headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
        assert "traceparent" not in headers and "x-amzn-trace-id" not in headers
    data = reader.get_metrics_data()
    assert data is not None
    serialized = data.to_json()
    assert "rboto.client.operation.duration" in serialized
    assert "private-table" not in serialized and "request-2" not in serialized


@service("s3")
@pytest.mark.asyncio
async def test_span_ends_before_body_consumption_without_eager_read(
    peer: Peer, telemetry: tuple[TracerProvider, InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    from rboto import S3Client
    from rboto_s3.types import GetObjectOutput

    _, exporter, _ = telemetry
    peer.body_gate = threading.Event()
    peer.replies = [(200, b"private-body")]
    client = S3Client(region="us-east-1", endpoint_url=peer.endpoint, config=config())
    output = await asyncio.wait_for(
        client.get_object(bucket="private-bucket", key="private-key"), timeout=2,
    )
    assert isinstance(output, GetObjectOutput)
    spans = exporter.get_finished_spans()
    assert len(spans) == 1 and spans[0].end_time is not None
    assert "private-" not in spans[0].to_json()
    assert output.body is not None
    read = asyncio.ensure_future(output.body.read())
    await asyncio.sleep(0.02)
    assert not read.done()
    peer.body_gate.set()
    assert await read == b"private-body"
    assert len(exporter.get_finished_spans()) == 1


@service("lambda")
@pytest.mark.asyncio
async def test_session_override_existing_client_and_modeled_error(
    peer: Peer, telemetry: tuple[TracerProvider, InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    from rboto import LambdaClient
    from rboto_lambda.exceptions import ResourceNotFoundError

    provider, exporter, _ = telemetry
    client = LambdaClient(
        session=Session(
            region="us-east-1", endpoint_url=peer.endpoint, config=config(),
            interceptors=(OpenTelemetry(tracer_provider=provider, metrics=False),),
        ),
    )
    peer.replies = [
        (200, b'{"Functions":[{"FunctionName":"private-function"}]}'),
        (404, b'{"Type":"User","message":"private-error"}'),
    ]
    functions = (await client.list_functions()).functions
    assert functions is not None and functions[0].function_name == "private-function"
    configure_opentelemetry(enabled=False)
    with pytest.raises(ResourceNotFoundError):
        await client.get_function(function_name="private-function")
    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert spans[1].status.status_code == StatusCode.ERROR
    assert "private-" not in "".join(span.to_json() for span in spans)


@service("lambda")
@pytest.mark.asyncio
async def test_cancelled_native_request_does_not_leak_context_or_retry(
    peer: Peer, telemetry: tuple[TracerProvider, InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    from rboto import LambdaClient

    _, exporter, _ = telemetry
    peer.request_gate = threading.Event()
    peer.replies = [(200, b'{"Functions":[]}')]
    client = LambdaClient(region="us-east-1", endpoint_url=peer.endpoint, config=config())
    task = asyncio.create_task(client.list_functions())
    assert await asyncio.to_thread(peer.received.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes is not None
    assert spans[0].attributes["rboto.operation.outcome"] == "cancelled"
    assert spans[0].status.status_code == StatusCode.UNSET
    assert not trace.get_current_span().get_span_context().is_valid
    peer.request_gate.set()
    await asyncio.sleep(0.05)
    assert len(peer.headers) == 1 and len(exporter.get_finished_spans()) == 1


@service("dynamodb")
def test_default_compiled_client_never_imports_opentelemetry(peer: Peer) -> None:
    peer.replies = [(200, b'{"TableNames":["example"]}')]
    program = """
import asyncio
import importlib.abc
import sys

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("opentelemetry"):
            raise ImportError("optional dependency is unavailable")
sys.meta_path.insert(0, Block())
from rboto import ClientConfig, DynamoDBClient
client = DynamoDBClient(
    region="us-east-1", endpoint_url=sys.argv[1],
    config=ClientConfig(access_key_id="testing", secret_access_key="testing", max_attempts=1),
)
assert asyncio.run(client.list_tables()).table_names == ["example"]
assert not any(name.startswith("opentelemetry") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", program, peer.endpoint],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@service("s3")
@service("dynamodb")
@pytest.mark.asyncio
async def test_concurrent_wheels_keep_independent_child_spans(
    peer: Peer, telemetry: tuple[TracerProvider, InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    from rboto import DynamoDBClient, S3Client

    provider, exporter, _ = telemetry
    body = b'{"TableNames":["example"]}'
    peer.replies = [(200, body), (200, body)]
    identities: dict[str, int] = {}

    async def probe(request: OperationRequest, call_next: Next) -> OperationResponse:
        identity = trace.get_current_span().get_span_context()
        identities[request.context.service] = identity.span_id
        result = await call_next(request)
        assert trace.get_current_span().get_span_context() == identity
        return result

    session = Session(
        region="us-east-1", endpoint_url=peer.endpoint, config=config(), interceptors=(probe,),
    )
    s3, dynamodb = S3Client(session=session), DynamoDBClient(session=session)
    with provider.get_tracer("application").start_as_current_span("parent") as parent:
        root = parent.get_span_context()
        obj, tables = await asyncio.gather(
            s3.get_object(bucket="private-bucket", key="private-key"), dynamodb.list_tables(),
        )
        assert trace.get_current_span() is parent
    assert tables.table_names == ["example"]
    assert obj.body is not None and await obj.body.read() == body
    assert len(set(identities.values())) == 2
    children = [span for span in exporter.get_finished_spans() if span.name != "parent"]
    assert {span.name for span in children} == {"S3.GetObject", "DynamoDB.ListTables"}
    for span in children:
        assert span.parent is not None and span.parent.span_id == root.span_id
        assert span.context is not None and span.context.trace_id == root.trace_id


@service("dynamodb")
@pytest.mark.asyncio
async def test_global_configuration_applies_to_existing_compiled_clients(
    peer: Peer, telemetry: tuple[TracerProvider, InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    from rboto import DynamoDBClient

    provider, exporter, _ = telemetry
    configure_opentelemetry(enabled=False)
    client = DynamoDBClient(region="us-east-1", endpoint_url=peer.endpoint, config=config())
    peer.replies = [(200, b'{"TableNames":[]}')] * 2
    configure_opentelemetry(tracer_provider=provider, metrics=False)
    await client.list_tables()
    assert len(exporter.get_finished_spans()) == 1
    configure_opentelemetry(enabled=False)
    await client.list_tables()
    assert len(exporter.get_finished_spans()) == 1
