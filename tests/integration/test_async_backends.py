"""Native scheduler, cancellation and stream contracts against local HTTP peers."""
from __future__ import annotations

import base64
import contextlib
import contextvars
import json
import os
import select
import socket
import struct
import threading
import time
import traceback
import zlib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import pytest
import rboto
from rboto import ClientConfig, WaiterConfig
from rboto.exceptions import ClientClosedError, StreamClosedError
from rboto_codegen.registry import list_services

CONFIG = ClientConfig(
    access_key_id="testing", secret_access_key="testing",
    max_attempts=1, operation_timeout=4,
)
CASES = {}
DETAILS = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


@dataclass
class Reply:
    body: bytes = b""
    status: int = 200
    content_type: str = "application/json"
    headers_gate: threading.Event | None = None
    chunks: tuple[bytes, bytes] | None = None
    body_gate: threading.Event | None = None


@dataclass
class Peer:
    reply: object = field(default_factory=Reply)
    requests: list = field(default_factory=list)
    received: threading.Event = field(default_factory=threading.Event)
    first_sent: threading.Event = field(default_factory=threading.Event)
    stopping: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    connections: int = 0
    endpoint: str = ""
    server_errors: list = field(default_factory=list)
    duplex: bool = False

    def __enter__(self):
        state = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(6)
                with state.lock:
                    state.connections += 1

            def finish(self):
                try:
                    super().finish()
                finally:
                    with state.lock:
                        state.connections -= 1

            def log_message(self, *args):
                pass

            def gate(self, gate):
                if gate is None:
                    return True
                deadline = time.monotonic() + 6
                while not gate.is_set() and not state.stopping.is_set():
                    if time.monotonic() > deadline:
                        return False
                    ready, _, _ = select.select([self.connection], [], [], 0.01)
                    if ready:
                        try:
                            if not self.connection.recv(1, socket.MSG_PEEK):
                                self.close_connection = True
                                return False
                        except OSError:
                            return False
                return gate.is_set()

            def read_body(self):
                if self.headers.get("Transfer-Encoding", "").lower() != "chunked":
                    return self.rfile.read(int(self.headers.get("Content-Length", "0")))
                chunks = []
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return b"".join(chunks)
                    size = int(line.split(b";")[0], 16)
                    if not size:
                        while self.rfile.readline() not in (b"\r\n", b""):
                            pass
                        return b"".join(chunks)
                    chunks.append(self.rfile.read(size))
                    if self.rfile.read(2) != b"\r\n":
                        return b"".join(chunks)

            def respond(self):
                try:
                    request = {
                        "method": self.command, "path": self.path,
                        "headers": {key.lower(): value for key, value in self.headers.items()},
                        "body": b"",
                    }
                    with state.lock:
                        index = len(state.requests)
                        state.requests.append(request)
                    state.received.set()
                    if state.duplex:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/vnd.amazon.eventstream")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        frame = event_frame(b"ready")
                        self.wfile.write(f"{len(frame):x}\r\n".encode() + frame + b"\r\n")
                        self.wfile.flush()
                        request["body"] = self.read_body()
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                        return
                    request["body"] = self.read_body()
                    reply = state.reply(request, index) if callable(state.reply) else state.reply
                    if not self.gate(reply.headers_gate):
                        return
                    self.send_response(reply.status)
                    self.send_header("Content-Type", reply.content_type)
                    self.send_header("x-amzn-requestid", f"probe-{index}")
                    self.send_header("x-amz-request-id", f"probe-{index}")
                    if reply.chunks is not None and self.command != "HEAD":
                        self.send_header("Transfer-Encoding", "chunked")
                    else:
                        self.send_header("Content-Length", str(len(reply.body)))
                    self.end_headers()
                    if self.command == "HEAD":
                        return
                    if reply.chunks is None:
                        self.wfile.write(reply.body)
                        self.wfile.flush()
                        return
                    first, last = reply.chunks
                    if first:
                        self.wfile.write(f"{len(first):x}\r\n".encode() + first + b"\r\n")
                        self.wfile.flush()
                    state.first_sent.set()
                    if not self.gate(reply.body_gate):
                        return
                    if last:
                        self.wfile.write(f"{len(last):x}\r\n".encode() + last + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    self.close_connection = True

            do_HEAD = do_GET = do_POST = do_PUT = respond

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, address):
                state.server_errors.append(traceback.format_exc())

        self.server = Server(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True,
        )
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def new_client(peer, cls=None, **kwargs):
    cls = rboto.S3Client if cls is None else cls
    return cls(region="eu-west-2", endpoint_url=peer.endpoint, config=CONFIG, **kwargs)


@contextlib.asynccontextmanager
async def managed(peer, cls=None, **kwargs):
    client = new_client(peer, cls, **kwargs)
    failed = False
    try:
        yield client
    except BaseException:
        failed = True
        raise
    finally:
        try:
            with anyio.CancelScope(shield=True):
                await client.aclose()
        except BaseException as error:
            DETAILS.setdefault("cleanup_errors", []).append(f"{type(error).__name__}: {error}")
            if not failed:
                raise


async def until(predicate, timeout=2):
    with anyio.fail_after(timeout):
        while not predicate():
            await anyio.sleep(0.005)


async def disconnected(peer):
    await until(lambda: peer.connections == 0)


def event_frame(data):
    headers = bytearray()
    for name, value in {
        ":message-type": "event", ":event-type": "chunk", ":content-type": "application/json",
    }.items():
        key, text = name.encode(), value.encode()
        headers += bytes([len(key)]) + key + b"\x07" + struct.pack(">H", len(text)) + text
    body = json.dumps({"bytes": base64.b64encode(data).decode()}).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    message = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


@case
async def backend_control(peer):
    values = []
    async def child(n):
        await anyio.sleep(0)
        values.append(n)
    async with anyio.create_task_group() as group:
        for n in range(5):
            group.start_soon(child, n)
    assert sorted(values) == list(range(5))
    with anyio.move_on_after(0.01) as scope:
        await anyio.sleep_forever()
    assert scope.cancelled_caught


@case
async def unused_client_close(peer):
    client = new_client(peer)
    await client.aclose()
    await client.aclose()
    assert not peer.requests


@case
async def s3_typed_call(peer):
    from rboto_s3.types import HeadObjectOutput
    async with managed(peer) as client:
        output = await client.head_object(bucket="bucket", key="key")
        assert isinstance(output, HeadObjectOutput)
        assert output.content_length == 0
        assert output.to_dict()["content_length"] == 0
    assert len(peer.requests) == 1
    assert peer.requests[0]["headers"].get("authorization")
    await disconnected(peer)


@case
async def dynamodb_typed_call(peer):
    from rboto_dynamodb.types import GetItemOutput
    peer.reply = Reply(body=b'{"Item":{"pk":{"S":"one"},"count":{"N":"42"}}}')
    async with managed(peer, rboto.DynamoDBClient) as client:
        output = await client.get_item(table_name="example", key={"pk": {"s": "one"}})
        assert isinstance(output, GetItemOutput)
        assert output.item == {"pk": {"s": "one"}, "count": {"n": "42"}}
    wire = json.loads(peer.requests[0]["body"])
    assert wire["Key"] == {"pk": {"S": "one"}}
    await disconnected(peer)


@case
async def bedrock_typed_call(peer):
    peer.reply = Reply(body=json.dumps({
        "output": {"message": {"role": "assistant", "content": [{"text": "local"}]}},
        "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1},
    }).encode())
    async with managed(peer, rboto.BedrockRuntimeClient) as client:
        output = await client.converse(
            model_id="local-model", messages=[{"role": "user", "content": [{"text": "hello"}]}],
        )
        assert output.output["message"]["content"] == [{"text": "local"}]
        assert output.usage.total_tokens == 2
    await disconnected(peer)


@case
async def concurrent_clients(peer):
    peer.reply = lambda req, n: (
        Reply() if req["method"] == "HEAD" else Reply(body=b'{"TableNames":["one"]}')
    )
    values = []
    async with managed(peer) as objects, managed(peer, rboto.DynamoDBClient) as database:
        async def child(n):
            if n % 2:
                assert (await database.list_tables()).table_names == ["one"]
            else:
                assert (await objects.head_object(bucket="bucket", key=str(n))).content_length == 0
            values.append(n)
        async with anyio.create_task_group() as group:
            for n in range(12):
                group.start_soon(child, n)
    assert sorted(values) == list(range(12))
    assert len(peer.requests) == 12
    await disconnected(peer)


@case
async def modeled_error(peer):
    from rboto_dynamodb.exceptions import ResourceNotFoundError
    peer.reply = Reply(status=400, body=b'{"__type":"ResourceNotFoundException","message":"gone"}')
    async with managed(peer, rboto.DynamoDBClient) as client:
        try:
            await client.describe_table(table_name="missing")
        except ResourceNotFoundError as error:
            assert error.error_code == "ResourceNotFoundException"
            assert error.request_id == "probe-0"
        else:
            raise AssertionError("modeled error did not propagate")
    await disconnected(peer)


@case
async def paginator(peer):
    peer.reply = lambda req, n: Reply(body=(
        b'{"TableNames":["one"],"LastEvaluatedTableName":"one"}'
        if n == 0 else b'{"TableNames":["two"]}'
    ))
    async with managed(peer, rboto.DynamoDBClient) as client:
        pages = [page async for page in client.paginators.list_tables(limit=1)]
        assert [page.table_names for page in pages] == [["one"], ["two"]]
    assert json.loads(peer.requests[1]["body"])["ExclusiveStartTableName"] == "one"
    await disconnected(peer)


@case
async def waiter(peer):
    peer.reply = lambda req, n: Reply(body=json.dumps({
        "Table": {"TableName": "example", "TableStatus": "CREATING" if n == 0 else "ACTIVE"},
    }).encode())
    async with managed(peer, rboto.DynamoDBClient) as client:
        output = await client.waiters.table_exists(
            table_name="example", waiter_config=WaiterConfig(delay=0.01, max_attempts=3),
        )
        assert output.table.table_status == "ACTIVE"
    assert len(peer.requests) == 2
    await disconnected(peer)


@case
async def middleware_context(peer):
    trace = contextvars.ContextVar("trace", default="absent")
    seen = []
    async def middleware(request, call_next):
        before = trace.get()
        await anyio.sleep(0)
        output = await call_next(request)
        seen.append((before, trace.get(), request.metadata.status_code))
        return output
    trace.set("caller-context")
    async with managed(peer, interceptors=[middleware]) as client:
        await client.head_object(bucket="bucket", key="key")
    assert seen == [("caller-context", "caller-context", 200)]
    await disconnected(peer)


@case
async def timeout_pending_rpc(peer):
    peer.reply = Reply(headers_gate=threading.Event())
    async with managed(peer) as client:
        started = time.monotonic()
        try:
            with anyio.fail_after(0.15):
                await client.head_object(bucket="bucket", key="slow")
        except TimeoutError:
            pass
        else:
            raise AssertionError("deadline did not interrupt RPC")
        assert peer.received.is_set()
        await disconnected(peer)
        DETAILS["cancel_to_disconnect_bound_ms"] = (time.monotonic() - started) * 1000
        peer.reply = Reply()
        assert (await client.head_object(bucket="bucket", key="reuse")).content_length == 0
    await disconnected(peer)


@case
async def cancelled_client_context(peer):
    peer.reply = Reply(headers_gate=threading.Event())
    client = new_client(peer)
    finalized = anyio.Event()
    async def child():
        try:
            async with client:
                await client.head_object(bucket="bucket", key="slow")
        finally:
            finalized.set()
    async with anyio.create_task_group() as group:
        group.start_soon(child)
        await until(peer.received.is_set)
        group.cancel_scope.cancel()
    assert finalized.is_set()
    await disconnected(peer)
    try:
        await client.head_object(bucket="bucket", key="closed")
    except ClientClosedError:
        pass
    else:
        raise AssertionError("cancelled context did not close client")
    with anyio.CancelScope(shield=True):
        await client.aclose()


@case
async def sibling_failure_cancels_rpc(peer):
    peer.reply = Reply(headers_gate=threading.Event())
    async with managed(peer) as client:
        async def rpc():
            await client.head_object(bucket="bucket", key="slow")
        async def failure():
            await until(peer.received.is_set)
            raise LookupError("sibling failed")
        caught = []
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(rpc)
                group.start_soon(failure)
        except* LookupError as error:
            caught.extend(error.exceptions)
        assert len(caught) == 1
        await disconnected(peer)


@case
async def close_pending_client(peer):
    peer.reply = Reply(headers_gate=threading.Event())
    async with managed(peer) as client:
        stopped = []
        async def rpc():
            try:
                await client.head_object(bucket="bucket", key="slow")
            except ClientClosedError:
                stopped.append(True)
        async with anyio.create_task_group() as group:
            group.start_soon(rpc)
            await until(peer.received.is_set)
            await client.aclose()
        assert stopped == [True]
    await disconnected(peer)


@case
async def body_outlives_client(peer):
    peer.reply = Reply(chunks=(b"first", b"last"))
    client = new_client(peer)
    response = await client.get_object(bucket="bucket", key="key")
    await client.aclose()
    async with response.body:
        assert await response.body.read() == b"firstlast"
    assert await response.body.read() == b""
    await disconnected(peer)


@case
async def cancel_body_iterator_and_resume(peer):
    gate = threading.Event()
    peer.reply = Reply(chunks=(b"first", b"last"), body_gate=gate)
    async with managed(peer) as client:
        response = await client.get_object(bucket="bucket", key="key")
        async with response.body as body:
            assert await anext(body) == b"first"
            with anyio.move_on_after(0.05) as scope:
                await anext(body)
            assert scope.cancelled_caught
            gate.set()
            assert await anext(body) == b"last"
    await disconnected(peer)


@case
async def cancel_body_collect(peer):
    peer.reply = Reply(chunks=(b"first", b"last"), body_gate=threading.Event())
    async with managed(peer) as client:
        response = await client.get_object(bucket="bucket", key="key")
        async with response.body as body:
            with anyio.move_on_after(0.05) as scope:
                await body.read()
            assert scope.cancelled_caught
            assert await body.read() == b""
        await disconnected(peer)


@case
async def close_pending_body(peer):
    peer.reply = Reply(chunks=(b"first", b"last"), body_gate=threading.Event())
    async with managed(peer) as client:
        response = await client.get_object(bucket="bucket", key="key")
        body = response.body
        assert await anext(body) == b"first"
        entered = anyio.Event()
        stopped = []
        async def read():
            entered.set()
            try:
                await body.read()
            except StreamClosedError:
                stopped.append(True)
        async with anyio.create_task_group() as group:
            group.start_soon(read)
            await entered.wait()
            await anyio.sleep(0.02)
            await body.aclose()
        assert stopped == [True]
        assert await body.read() == b""
    await disconnected(peer)


@case
async def event_stream_cancel_and_resume(peer):
    gate = threading.Event()
    peer.reply = Reply(
        chunks=(event_frame(b"first"), event_frame(b"last")), body_gate=gate,
        content_type="application/vnd.amazon.eventstream",
    )
    async with managed(peer, rboto.BedrockRuntimeClient) as client:
        output = await client.invoke_model_with_response_stream(model_id="local", body=b"{}")
        stream = output["body"]
        async with stream:
            assert await anext(stream) == {"chunk": {"bytes": b"first"}}
            with anyio.move_on_after(0.05) as scope:
                await anext(stream)
            assert scope.cancelled_caught
            gate.set()
            assert await anext(stream) == {"chunk": {"bytes": b"last"}}
    await disconnected(peer)


@case
async def event_stream_close_pending(peer):
    peer.reply = Reply(
        chunks=(event_frame(b"first"), event_frame(b"last")), body_gate=threading.Event(),
        content_type="application/vnd.amazon.eventstream",
    )
    async with managed(peer, rboto.BedrockRuntimeClient) as client:
        output = await client.invoke_model_with_response_stream(model_id="local", body=b"{}")
        stream = output["body"]
        assert await anext(stream) == {"chunk": {"bytes": b"first"}}
        entered = anyio.Event()
        stopped = []
        async def receive():
            entered.set()
            try:
                await anext(stream)
            except StopAsyncIteration:
                stopped.append(True)
        async with anyio.create_task_group() as group:
            group.start_soon(receive)
            await entered.wait()
            await anyio.sleep(0.02)
            await stream.aclose()
        assert stopped == [True]
    await disconnected(peer)


@case
async def streamed_upload(peer):
    trace = contextvars.ContextVar("source-trace", default="missing")
    trace.set("from-caller")
    seen = []
    async def source():
        for data in (b"hello-", b"world"):
            await anyio.sleep(0.005)
            seen.append(trace.get())
            yield data
    async with managed(peer) as client:
        await client.put_object(
            bucket="bucket", key="key", body=source(), content_length=11,
        )
    assert seen == ["from-caller", "from-caller"]
    # The SDK may add AWS streaming checksum framing around these bytes.
    assert b"hello-" in peer.requests[0]["body"] and b"world" in peer.requests[0]["body"]
    await disconnected(peer)


@case
async def cancel_upload_python_poll(peer):
    polling = anyio.Event()
    finalized = anyio.Event()
    async def source():
        try:
            yield b"first"
            polling.set()
            await anyio.sleep_forever()
        finally:
            finalized.set()
    chunks = source()
    async with managed(peer) as client:
        async def upload():
            await client.put_object(bucket="bucket", key="key", body=chunks, content_length=100)
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(upload)
                await polling.wait()
                group.cancel_scope.cancel()
            with anyio.fail_after(2):
                await finalized.wait()
            await disconnected(peer)
        finally:
            await chunks.aclose()


@case
async def bidirectional_event_close_cancels_input(peer):
    peer.duplex = True
    polling = anyio.Event()
    finalized = anyio.Event()
    async def source():
        try:
            yield {"chunk": {"bytes": b"first"}}
            polling.set()
            await anyio.sleep_forever()
        finally:
            finalized.set()
    events = source()
    async with managed(peer, rboto.BedrockRuntimeClient) as client:
        try:
            with anyio.fail_after(3):
                output = await client.invoke_model_with_bidirectional_stream(
                    model_id="local", body=events,
                )
                stream = output["body"]
                assert await anext(stream) == {"chunk": {"bytes": b"ready"}}
                await polling.wait()
                await stream.aclose()
                await client.aclose()
                await finalized.wait()
                await disconnected(peer)
        finally:
            await events.aclose()


@case
async def upload_producer_error(peer):
    error = LookupError("producer failed")
    async def source():
        yield b"first"
        await anyio.sleep(0)
        raise error
    async with managed(peer) as client:
        try:
            await client.put_object(bucket="bucket", key="key", body=source(), content_length=100)
        except LookupError as caught:
            assert caught is error
        else:
            raise AssertionError("producer error not preserved")
    await disconnected(peer)


@case
async def waiter_external_cancel(peer):
    peer.reply = Reply(body=b'{"Table":{"TableName":"example","TableStatus":"CREATING"}}')
    async with managed(peer, rboto.DynamoDBClient) as client:
        with anyio.move_on_after(0.15) as scope:
            await client.waiters.table_exists(
                table_name="example", waiter_config=WaiterConfig(delay=10, max_attempts=10),
            )
        assert scope.cancelled_caught
        assert len(peer.requests) == 1
        peer.reply = Reply(body=b'{"TableNames":["reuse"]}')
        assert (await client.list_tables()).table_names == ["reuse"]
    await disconnected(peer)


@case
async def paginator_cancel_next_page(peer):
    peer.reply = lambda req, n: (
        Reply(body=b'{"TableNames":["one"],"LastEvaluatedTableName":"one"}') if n == 0
        else Reply(body=b'{"TableNames":["two"]}', headers_gate=threading.Event())
    )
    async with managed(peer, rboto.DynamoDBClient) as client:
        pages = client.paginators.list_tables(limit=1)
        assert (await anext(pages)).table_names == ["one"]
        with anyio.move_on_after(0.05) as scope:
            await anext(pages)
        assert scope.cancelled_caught
        assert len(peer.requests) == 2
        await disconnected(peer)



@case
async def completion_cancel_races(peer):
    async with managed(peer) as client:
        await client.head_object(bucket="bucket", key="warm")
        completed = []
        cancelled = []
        for index in range(80):
            async def request():
                try:
                    output = await client.head_object(bucket="bucket", key="race")
                    assert output.content_length == 0
                    completed.append(True)
                except anyio.get_cancelled_exc_class():
                    cancelled.append(True)
                    raise
            async with anyio.create_task_group() as group:
                group.start_soon(request)
                await anyio.sleep((0, 0.0001, 0.001, 0.003)[index % 4])
                group.cancel_scope.cancel()
        assert len(completed) + len(cancelled) == 80
        assert (await client.head_object(bucket="bucket", key="reuse")).content_length == 0
    await disconnected(peer)


@case
async def logging_and_otel_cancellation(peer):
    import io

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode
    from rboto import configure_logging
    from rboto.integrations.opentelemetry import OpenTelemetry

    logs = io.StringIO()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observer = OpenTelemetry(tracer_provider=provider, metrics=False)
    configure_logging(level="INFO", stream=logs)
    try:
        with provider.get_tracer("test").start_as_current_span("parent") as parent:
            async with managed(peer, interceptors=[observer]) as client:
                await client.head_object(bucket="bucket", key="success")
                assert trace.get_current_span() is parent
                peer.reply = Reply(headers_gate=threading.Event())
                with anyio.move_on_after(0.05) as scope:
                    await client.head_object(bucket="bucket", key="cancel")
                assert scope.cancelled_caught
                assert trace.get_current_span() is parent
        operations = [span for span in exporter.get_finished_spans() if span.name != "parent"]
        assert [span.attributes["rboto.operation.outcome"] for span in operations] == [
            "success", "cancelled",
        ]
        assert all(span.status.status_code != StatusCode.ERROR for span in operations)
        assert all(span.parent.span_id == parent.get_span_context().span_id for span in operations)
        records = [json.loads(line) for line in logs.getvalue().splitlines()]
        assert [record["outcome"] for record in records] == ["success", "cancelled"]
        assert records[1]["level"] == "WARNING"
    finally:
        provider.shutdown()
    await disconnected(peer)


@case
async def waiter_internal_deadline(peer):
    from rboto.waiters import WaiterTimeoutError

    peer.reply = Reply(body=b'{"Table":{"TableStatus":"CREATING"}}')
    async with managed(peer, rboto.DynamoDBClient) as client:
        with pytest.raises(WaiterTimeoutError):
            await client.waiters.table_exists(
                table_name="example", waiter_config=WaiterConfig(delay=10, timeout=0.05),
            )
        assert len(peer.requests) == 1
    await disconnected(peer)


_SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
_DYNAMODB = {"dynamodb_typed_call", "modeled_error", "paginator", "waiter",
             "waiter_external_cancel", "paginator_cancel_next_page", "waiter_internal_deadline"}
_BEDROCK = {"bedrock_typed_call", "event_stream_cancel_and_resume", "event_stream_close_pending",
            "bidirectional_event_close_cancels_input"}


def required_services(name):
    if name == "backend_control":
        return set()
    if name == "concurrent_clients":
        return {"s3", "dynamodb"}
    if name in _DYNAMODB:
        return {"dynamodb"}
    if name in _BEDROCK:
        return {"bedrock-runtime"}
    return {"s3"}


@pytest.fixture
def local_peer(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("AWS_") or key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            monkeypatch.delenv(key)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    monkeypatch.setenv("NO_PROXY", "*")
    DETAILS.clear()
    with Peer() as peer:
        yield peer


@pytest.mark.usefixtures("json_logging")
@pytest.mark.parametrize("backend", ["asyncio", "trio"])
@pytest.mark.parametrize("name", [
    pytest.param(name, marks=pytest.mark.skipif(
        bool(_SELECTED and not required_services(name) <= _SELECTED),
        reason="required native service not selected",
    )) for name in CASES
])
def test_native_backend_contract(local_peer, backend, name):
    anyio.run(CASES[name], local_peer, backend=backend)
    assert local_peer.connections == 0
    assert not local_peer.server_errors
    assert not DETAILS.get("cleanup_errors")


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
@pytest.mark.parametrize("service", [
    service for service in list_services()
    if not _SELECTED or service.service_id in _SELECTED
], ids=lambda service: service.service_id)
def test_every_selected_client_lifecycle_backend(service, backend):
    import importlib
    cls = getattr(importlib.import_module(service.python_package), service.client_class)
    async def main():
        client = cls(region="eu-west-2")
        async with client:
            pass
        await client.aclose()
        with pytest.raises(ClientClosedError):
            async with client:
                pass
    anyio.run(main, backend=backend)


@pytest.mark.skipif(bool(_SELECTED and "s3" not in _SELECTED), reason="S3 not selected")
def test_same_client_across_trio_and_asyncio_threads(local_peer):
    from concurrent.futures import ThreadPoolExecutor

    client = new_client(local_peer)
    async def requests():
        for _ in range(12):
            assert (await client.head_object(bucket="bucket", key="thread")).content_length == 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        threads = [
            pool.submit(anyio.run, requests, backend=backend)
            for backend in ("asyncio", "trio")
        ]
        for thread in threads:
            thread.result(timeout=10)
    anyio.run(client.aclose, backend="trio")
    anyio.run(disconnected, local_peer, backend="asyncio")
    assert len(local_peer.requests) == 24


@pytest.mark.skipif(bool(_SELECTED and "s3" not in _SELECTED), reason="S3 not selected")
def test_trio_guest_task_in_asyncio_host_uses_trio_bridge(local_peer):
    import asyncio

    import trio

    async def guest():
        assert asyncio.get_running_loop() is not None
        async with new_client(local_peer) as client:
            assert (await client.head_object(bucket="bucket", key="guest")).content_length == 0
        await disconnected(local_peer)

    async def host():
        loop = asyncio.get_running_loop()
        done = loop.create_future()
        trio.lowlevel.start_guest_run(
            guest, run_sync_soon_threadsafe=loop.call_soon_threadsafe,
            done_callback=done.set_result,
        )
        result = await done
        result.unwrap()

    asyncio.run(host())
