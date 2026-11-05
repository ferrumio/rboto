"""Native cleanup, cancellation, and ownership against controlled HTTP peers."""

from __future__ import annotations

import asyncio
import base64
import importlib
import inspect
import json
import os
import select
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rboto import ClientConfig, Next, OperationRequest, OperationResponse
from rboto.exceptions import ClientClosedError, StreamClosedError
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import list_services

if TYPE_CHECKING:
    from rboto import S3Client

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
SERVICES = [item for item in list_services() if not SELECTED or item.service_id in SELECTED]
pytestmark = pytest.mark.asyncio
s3_only = pytest.mark.skipif(bool(SELECTED and "s3" not in SELECTED), reason="S3 not selected")
bedrock_only = pytest.mark.skipif(
    bool(SELECTED and "bedrock-runtime" not in SELECTED), reason="Bedrock Runtime not selected",
)


@pytest.mark.parametrize("service", SERVICES, ids=lambda item: item.service_id)
async def test_every_client_closes_and_rejects_every_operation(service: ServiceDescriptor) -> None:
    package = importlib.import_module(service.python_package)
    cls = getattr(package, service.client_class)
    client = cls(region="us-east-1")
    async with client as entered:
        assert entered is client
    await asyncio.gather(*(client.aclose() for _ in range(4)))
    with pytest.raises(ClientClosedError):
        async with client:
            pytest.fail("closed client entered")
    native = client._native
    for name, method in cls.__dict__.items():
        if not inspect.iscoroutinefunction(method) or name in {"aclose", "__aenter__", "__aexit__"}:
            continue
        required = {
            name: object() for name, argument in inspect.signature(method).parameters.items()
            if name != "self" and argument.default is inspect.Parameter.empty
        }
        # Closure takes precedence over conversion and never dispatches.
        with pytest.raises(ClientClosedError):
            await getattr(client, name)(**required)
        with pytest.raises(ClientClosedError):
            await getattr(native, name)({})


@pytest.mark.parametrize("service", SERVICES, ids=lambda item: item.service_id)
async def test_unused_context_exit_is_lazy_and_preserves_exception(
    service: ServiceDescriptor,
) -> None:
    code = """
import asyncio
import importlib
import sys
package = importlib.import_module(sys.argv[1])
native = importlib.import_module(sys.argv[1] + "._native")
cls = getattr(package, sys.argv[2])
error = LookupError("application failure")
async def main():
    try:
        async with cls() as client:
            assert native._runtime_worker_threads() is None
            raise error
    except LookupError as actual:
        assert actual is error
    else:
        raise AssertionError("context exit swallowed the exception")
    await client.aclose()
    assert native._runtime_worker_threads() is None
asyncio.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", code, service.python_package, service.client_class],
        text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


@dataclass
class Peer:
    endpoint: str = ""
    header_gate: threading.Event | None = None
    body_gate: threading.Event | None = None
    content_type: str = "application/octet-stream"
    first: bytes = b"first"
    last: bytes = b"last"
    requests: list[bytes] = field(default_factory=list)
    requested: threading.Event = field(default_factory=threading.Event)
    first_sent: threading.Event = field(default_factory=threading.Event)
    disconnected: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    connections: int = 0


@pytest.fixture
def peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Peer]:
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    state = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            with state.lock:
                state.connections += 1

        def finish(self) -> None:
            try:
                super().finish()
            finally:
                with state.lock:
                    state.connections -= 1
                state.disconnected.set()

        def log_message(self, format: str, *args: object) -> None:
            pass

        def gate(self, gate: threading.Event | None) -> bool:
            if gate is None:
                return True
            deadline = time.monotonic() + 10
            while not gate.is_set() and time.monotonic() < deadline:
                readable, _, _ = select.select([self.connection], [], [], 0.02)
                if readable:
                    try:
                        if not self.connection.recv(1, socket.MSG_PEEK):
                            self.close_connection = True
                            return False
                    except ConnectionError:
                        self.close_connection = True
                        return False
            return gate.is_set()

        def respond(self) -> None:
            request = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.requests.append(request)
            state.requested.set()
            if not self.gate(state.header_gate):
                return
            self.send_response(200)
            if self.command == "HEAD":
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_header("Content-Type", state.content_type)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                if state.first:
                    self.wfile.write(
                        f"{len(state.first):x}\r\n".encode() + state.first + b"\r\n",
                    )
                    self.wfile.flush()
                state.first_sent.set()
                if not self.gate(state.body_gate):
                    return
                if state.last:
                    self.wfile.write(f"{len(state.last):x}\r\n".encode() + state.last + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        do_GET = respond
        do_POST = respond
        do_HEAD = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield state
    finally:
        if state.header_gate:
            state.header_gate.set()
        if state.body_gate:
            state.body_gate.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def config() -> ClientConfig:
    return ClientConfig(access_key_id="testing", secret_access_key="testing", max_attempts=1)


def s3_client(peer: Peer) -> S3Client:
    from rboto import S3Client

    return S3Client(region="us-east-1", endpoint_url=peer.endpoint, config=config())


async def signalled(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 3)


async def connection_count(peer: Peer, expected: int) -> None:
    async with asyncio.timeout(3):
        while True:
            with peer.lock:
                if peer.connections == expected:
                    return
            await asyncio.sleep(0.01)


@s3_only
async def test_client_close_releases_idle_connection_and_keeps_outputs(peer: Peer) -> None:
    client = s3_client(peer)
    output = await client.head_object(bucket="bucket", key="key")
    await connection_count(peer, 1)
    await client.aclose()
    await connection_count(peer, 0)
    assert output.content_length == 0
    assert output.to_dict()["content_length"] == 0


@s3_only
async def test_client_close_interrupts_pending_requests_and_keeps_other_clients(peer: Peer) -> None:
    client = s3_client(peer)
    other = s3_client(peer)
    peer.header_gate = threading.Event()
    tasks = [asyncio.create_task(client.head_object(bucket="bucket", key="key")) for _ in range(4)]
    try:
        await signalled(peer.requested)
        async with asyncio.timeout(3):
            await asyncio.gather(*(client.aclose() for _ in range(4)))
            results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(result, ClientClosedError) for result in results)
        await connection_count(peer, 0)
        peer.header_gate.set()
        assert (await other.head_object(bucket="bucket", key="other")).content_length == 0
    finally:
        peer.header_gate.set()
        await client.aclose()
        await other.aclose()
        await asyncio.gather(*tasks, return_exceptions=True)


@s3_only
async def test_close_during_first_use_assume_role(
    peer: Peer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rboto import S3Client

    monkeypatch.setenv("AWS_ENDPOINT_URL_STS", peer.endpoint)
    peer.header_gate = threading.Event()
    client = S3Client(
        region="us-east-1", endpoint_url=peer.endpoint,
        config=ClientConfig(
            access_key_id="testing", secret_access_key="testing", max_attempts=1,
            role_arn="arn:aws:iam::123456789012:role/example",
        ),
    )
    task = asyncio.create_task(client.head_object(bucket="bucket", key="key"))
    try:
        await signalled(peer.requested)
        assert b"Action=AssumeRole" in peer.requests[0]
        await asyncio.wait_for(client.aclose(), 3)
        with pytest.raises(ClientClosedError):
            await task
        await connection_count(peer, 0)
        assert len(peer.requests) == 1
    finally:
        peer.header_gate.set()
        await client.aclose()
        await asyncio.gather(task, return_exceptions=True)


@s3_only
async def test_close_crosses_python_threads_and_event_loops(peer: Peer) -> None:
    client = s3_client(peer)
    peer.header_gate = threading.Event()

    def request() -> None:
        async def run() -> None:
            with pytest.raises(ClientClosedError):
                await client.head_object(bucket="bucket", key="key")
        asyncio.run(run())

    task = asyncio.create_task(asyncio.to_thread(request))
    try:
        await signalled(peer.requested)
        await asyncio.wait_for(client.aclose(), 3)
        await asyncio.wait_for(task, 3)
        await connection_count(peer, 0)
    finally:
        peer.header_gate.set()
        await client.aclose()
        await asyncio.gather(task, return_exceptions=True)


@s3_only
async def test_cancelled_close_keeps_terminal_state(peer: Peer) -> None:
    client = s3_client(peer)
    peer.header_gate = threading.Event()
    request = asyncio.create_task(client.head_object(bucket="bucket", key="key"))
    try:
        await signalled(peer.requested)
        # Calling the native method publishes the signal before its awaitable
        # can be cancelled, independent of which Tokio worker handles cleanup.
        closing = client._native.aclose()
        closing.cancel()  # type: ignore[attr-defined]
        await asyncio.wait_for(client.aclose(), 3)
        with pytest.raises(ClientClosedError):
            await request
        with pytest.raises(ClientClosedError):
            await client.head_object(bucket="bucket", key="key")
        await connection_count(peer, 0)
    finally:
        peer.header_gate.set()
        await client.aclose()
        await asyncio.gather(request, return_exceptions=True)


@s3_only
async def test_closed_client_rejects_middleware_short_circuit(peer: Peer) -> None:
    from rboto import S3Client

    called = False

    async def middleware(request: OperationRequest, call_next: Next) -> OperationResponse:
        nonlocal called
        called = True
        raise AssertionError("closed client entered middleware")

    client = S3Client(
        region="us-east-1", endpoint_url=peer.endpoint, config=config(), interceptors=[middleware],
    )
    await client.aclose()
    with pytest.raises(ClientClosedError):
        await client.head_object(bucket="bucket", key="key")
    assert not called
    assert not peer.requests


@s3_only
async def test_closed_client_is_rejected_by_paginators_and_waiters(peer: Peer) -> None:
    client = s3_client(peer)
    await client.aclose()
    with pytest.raises(ClientClosedError):
        await anext(client.paginators.list_objects_v2(bucket="bucket"))
    with pytest.raises(ClientClosedError):
        await client.waiters.bucket_exists(bucket="bucket")
    assert not peer.requests


@pytest.mark.skipif(
    bool(SELECTED and not {"s3", "sts"} <= SELECTED), reason="requires S3 and STS",
)
async def test_closing_a_client_keeps_another_service_wheel_usable(peer: Peer) -> None:
    from rboto import STSClient

    peer.content_type = "text/xml"
    peer.first = (
        b'<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
        b"<GetCallerIdentityResult><Arn>arn:aws:iam::123456789012:user/test</Arn>"
        b"<UserId>test</UserId><Account>123456789012</Account></GetCallerIdentityResult>"
        b"</GetCallerIdentityResponse>"
    )
    peer.last = b""
    objects = s3_client(peer)
    async with STSClient(
        region="us-east-1", endpoint_url=peer.endpoint, config=config(),
    ) as identity:
        assert (await identity.get_caller_identity()).account == "123456789012"
        await objects.head_object(bucket="bucket", key="key")
        await objects.aclose()
        assert (await identity.get_caller_identity()).account == "123456789012"
    await connection_count(peer, 0)


@s3_only
async def test_inflight_middleware_cannot_dispatch_after_close(peer: Peer) -> None:
    from rboto import S3Client

    entered = asyncio.Event()
    resume = asyncio.Event()

    async def middleware(request: OperationRequest, call_next: Next) -> OperationResponse:
        entered.set()
        await resume.wait()
        return await call_next(request)

    client = S3Client(
        region="us-east-1", endpoint_url=peer.endpoint, config=config(), interceptors=[middleware],
    )
    task = asyncio.create_task(client.head_object(bucket="bucket", key="key"))
    try:
        await entered.wait()
        await client.aclose()
        assert not task.done()
        resume.set()
        with pytest.raises(ClientClosedError):
            await task
        assert not peer.requests
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)


@s3_only
@pytest.mark.parametrize("consume_first", [False, True])
async def test_close_unread_and_partial_body_releases_connection(
    peer: Peer, consume_first: bool,
) -> None:
    peer.body_gate = threading.Event()
    client = s3_client(peer)
    response = await client.get_object(bucket="bucket", key="key")
    if consume_first:
        assert await anext(response.body) == b"first"
    await asyncio.gather(*(response.body.aclose() for _ in range(4)))
    await connection_count(peer, 0)
    assert await response.body.read() == b""
    with pytest.raises(StopAsyncIteration):
        await anext(response.body)
    with pytest.raises(StreamClosedError):
        async with response.body:
            pytest.fail("closed body entered")
    await client.aclose()


@s3_only
@pytest.mark.parametrize("collect", [False, True])
async def test_close_interrupts_pending_body_consumption(peer: Peer, collect: bool) -> None:
    peer.body_gate = threading.Event()
    client = s3_client(peer)
    response = await client.get_object(bucket="bucket", key="key")
    assert await anext(response.body) == b"first"
    pending = asyncio.ensure_future(response.body.read() if collect else anext(response.body))
    try:
        await asyncio.sleep(0.03)
        assert not pending.done()
        await asyncio.wait_for(response.body.aclose(), 3)
        with pytest.raises(StreamClosedError if collect else StopAsyncIteration):
            await asyncio.wait_for(pending, 3)
        await connection_count(peer, 0)
    finally:
        await response.body.aclose()
        await client.aclose()
        await asyncio.gather(pending, return_exceptions=True)


@s3_only
async def test_returned_body_outlives_client_and_context_exit_releases_it(peer: Peer) -> None:
    peer.body_gate = threading.Event()
    client = s3_client(peer)
    response = await client.get_object(bucket="bucket", key="key")
    await client.aclose()
    error = LookupError("application error")
    with pytest.raises(LookupError) as caught:
        async with response.body as body:
            assert body is response.body
            assert await anext(body) == b"first"
            raise error
    assert caught.value is error
    await connection_count(peer, 0)
    assert await response.body.read() == b""


def event_frame(data: bytes) -> bytes:
    headers = bytearray()
    for name, value in {
        ":message-type": "event", ":event-type": "chunk", ":content-type": "application/json",
    }.items():
        key, encoded = name.encode(), value.encode()
        headers += bytes([len(key)]) + key + b"\x07" + struct.pack(">H", len(encoded)) + encoded
    body = json.dumps({"bytes": base64.b64encode(data).decode()}).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    frame = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return frame + struct.pack(">I", zlib.crc32(frame))


@bedrock_only
@pytest.mark.parametrize("consume_first", [False, True])
async def test_event_receiver_close_releases_socket_and_interrupts_receive(
    peer: Peer, consume_first: bool,
) -> None:
    from rboto import BedrockRuntimeClient

    peer.body_gate = threading.Event()
    peer.content_type = "application/vnd.amazon.eventstream"
    peer.first = event_frame(b"first")
    client = BedrockRuntimeClient(
        region="us-east-1", endpoint_url=peer.endpoint, config=config(),
    )
    response = await client.invoke_model_with_response_stream(model_id="example", body=b"{}")
    receiver = response["body"]
    assert receiver is not None
    await client.aclose()
    pending = None
    if consume_first:
        assert await anext(receiver) == {"chunk": {"bytes": b"first"}}
        pending = asyncio.ensure_future(anext(receiver))
        await asyncio.sleep(0.03)
        assert not pending.done()
    await asyncio.gather(*(receiver.aclose() for _ in range(4)))
    if pending is not None:
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(pending, 3)
    with pytest.raises(StopAsyncIteration):
        await anext(receiver)
    with pytest.raises(StreamClosedError):
        async with receiver:
            pytest.fail("closed receiver entered")
    await connection_count(peer, 0)


@bedrock_only
async def test_event_context_exit_preserves_exception(peer: Peer) -> None:
    from rboto import BedrockRuntimeClient

    peer.body_gate = threading.Event()
    peer.content_type = "application/vnd.amazon.eventstream"
    peer.first = event_frame(b"first")
    async with BedrockRuntimeClient(
        region="us-east-1", endpoint_url=peer.endpoint, config=config(),
    ) as client:
        response = await client.invoke_model_with_response_stream(model_id="example", body=b"{}")
        receiver = response["body"]
        assert receiver is not None
        error = LookupError("consumer failed")
        with pytest.raises(LookupError) as caught:
            async with receiver as entered:
                assert entered is receiver
                assert await anext(entered) == {"chunk": {"bytes": b"first"}}
                raise error
        assert caught.value is error
        await connection_count(peer, 0)
