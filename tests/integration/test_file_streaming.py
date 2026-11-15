"""File sources and sinks through compiled service operations on both schedulers."""

import base64
import hashlib
import io
import os
import threading
import uuid
import zlib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anyio
import pytest
from rboto import ClientConfig, FileBody
from rboto.exceptions import ConnectionError as RbotoConnectionError
from rboto.exceptions import StreamClosedError

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(bool(SELECTED and "s3" not in SELECTED), reason="S3 not selected"),
]


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request):
    return request.param


@dataclass
class Peer:
    endpoint: str = ""
    data: bytes = bytes(range(256)) * 4096
    received: list[bytes] = field(default_factory=list)
    retry: bool = False
    change: str = ""
    source: Path | None = None
    pause: bool = False
    truncate: bool = False
    started: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=threading.Event)
    checksums: list[str] = field(default_factory=list)


@pytest.fixture
def peer(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_REQUEST_CHECKSUM_CALCULATION", "WHEN_REQUIRED")
    monkeypatch.setenv("AWS_RESPONSE_CHECKSUM_VALIDATION", "WHEN_REQUIRED")
    state = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass

        def do_PUT(self):
            try:
                state.started.set()
                if state.pause:
                    state.resume.wait(5)
                if self.headers.get("Transfer-Encoding") == "chunked":
                    parts = []
                    while size := int(self.rfile.readline().strip().split(b";")[0], 16):
                        parts.append(self.rfile.read(size))
                        assert self.rfile.read(2) == b"\r\n"
                    while self.rfile.readline().strip():
                        pass
                    body = b"".join(parts)
                else:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length)
                    if len(body) != length:
                        self.close_connection = True
                        return
                if "aws-chunked" in self.headers.get("Content-Encoding", ""):
                    stream = io.BytesIO(body)
                    parts = []
                    while size := int(stream.readline().strip().split(b";")[0], 16):
                        parts.append(stream.read(size))
                        assert stream.read(2) == b"\r\n"
                    while line := stream.readline().strip():
                        if line.startswith(b"x-amz-checksum-crc32:"):
                            state.checksums.append(line.split(b":", 1)[1].decode())
                    body = b"".join(parts)
                if self.headers.get("x-amz-checksum-crc32"):
                    state.checksums.append(self.headers["x-amz-checksum-crc32"])
                state.received.append(body)
                if state.retry and len(state.received) == 1:
                    if state.source is not None and state.change:
                        source = state.source
                        if state.change == "remove":
                            source.unlink()
                        elif state.change == "replace":
                            replacement = source.with_suffix(".new")
                            replacement.write_bytes(body)
                            replacement.replace(source)
                        else:
                            old_time = source.stat().st_mtime_ns
                            new = body[:-1] if state.change == "shorter" else body + b"x"
                            if state.change == "same-size":
                                new = bytes([body[0] ^ 1]) + body[1:]
                            source.write_bytes(new)
                            os.utime(source, ns=(old_time, old_time + 1_000_000))
                    error = b"<Error><Code>SlowDown</Code><Message>retry</Message></Error>"
                    self.send_response(503)
                    self.send_header("Content-Length", str(len(error)))
                    self.end_headers()
                    self.wfile.write(error)
                else:
                    self.send_response(202 if self.command == "POST" else 200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def do_POST(self):
            if self.path.startswith("/model/") or self.path == "/v1/speech":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.do_GET()
            else:
                self.do_PUT()

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(state.data)))
            self.send_header("x-amz-request-id", "file-test")
            self.end_headers()
            try:
                half = len(state.data) // 2
                self.wfile.write(state.data[:half])
                self.wfile.flush()
                state.started.set()
                if state.pause:
                    state.resume.wait(5)
                if state.truncate:
                    self.close_connection = True
                    return
                self.wfile.write(state.data[half:])
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        yield state
    finally:
        state.resume.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def client(peer, attempts=1):
    from rboto import S3Client

    return S3Client(
        endpoint_url=peer.endpoint, region="eu-west-2",
        config=ClientConfig(
            access_key_id="testing", secret_access_key="testing",
            max_attempts=attempts, read_timeout=5,
        ),
    )


@pytest.mark.parametrize("length", [0, 1, 65537, 2 * 1024**2 + 13])
async def test_round_trip_file(peer, tmp_path, length):
    data = (bytes(range(256)) * ((length + 255) // 256))[:length]
    source = tmp_path / "ação.bin"
    source.write_bytes(data)
    peer.data = data
    async with client(peer) as s3:
        await s3.put_object(bucket="test", key="source", body=FileBody(source))
        response = await s3.get_object(bucket="test", key="source")
        target = tmp_path / "download.bin"
        assert await response.body.write_to(target) == length
        assert await response.body.read() == b""
        assert await response.body.write_to(tmp_path / "exhausted") == 0
    assert peer.received == [data]
    assert target.read_bytes() == data


async def test_retries_use_fresh_complete_reader(peer, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(peer.data)
    peer.retry = True
    async with client(peer, attempts=2) as s3:
        await s3.put_object(bucket="test", key="source", body=FileBody(source))
    assert peer.received == [peer.data, peer.data]


@pytest.mark.parametrize("change", ["shorter", "longer", "same-size", "replace", "remove"])
async def test_changed_source_is_not_replayed(peer, tmp_path, change):
    source = tmp_path / "source"
    source.write_bytes(peer.data)
    peer.retry, peer.change, peer.source = True, change, source
    async with client(peer, attempts=2) as s3:
        with pytest.raises(OSError):
            await s3.put_object(bucket="test", key="source", body=FileBody(source))
    assert peer.received == [peer.data]


async def test_checksum_trailers(peer, tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REQUEST_CHECKSUM_CALCULATION", "WHEN_SUPPORTED")
    source = tmp_path / "source"
    source.write_bytes(peer.data)
    async with client(peer) as s3:
        await s3.put_object(
            bucket="test", key="source", body=FileBody(source), checksum_algorithm="CRC32",
        )
    assert peer.received == [peer.data]
    assert peer.checksums == [base64.b64encode(zlib.crc32(peer.data).to_bytes(4, "big")).decode()]


async def test_concurrent_readers_are_independent(peer, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(peer.data)
    body = FileBody(source)
    async with client(peer) as s3:
        async def upload():
            await s3.put_object(bucket="test", key=uuid.uuid4().hex, body=body)
        async with anyio.create_task_group() as group:
            for _ in range(4):
                group.start_soon(upload)
    assert peer.received == [peer.data] * 4


async def test_source_errors_and_length_validation(peer, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"abc")
    async with client(peer) as s3:
        with pytest.raises(FileNotFoundError):
            await s3.put_object(bucket="test", key="missing", body=FileBody(tmp_path / "missing"))
        with pytest.raises(ValueError, match="regular file"):
            await s3.put_object(bucket="test", key="directory", body=FileBody(tmp_path))
        for length in (2, 4):
            with pytest.raises(ValueError, match="body.*file size"):
                await s3.put_object(
                    bucket="test", key="length", body=FileBody(source), content_length=length,
                )
    assert not peer.received


async def test_destination_error_preserves_file_and_body(peer, tmp_path):
    target = tmp_path / "exists"
    target.write_bytes(b"keep")
    async with client(peer) as s3:
        response = await s3.get_object(bucket="test", key="source")
        with pytest.raises(FileExistsError):
            await response.body.write_to(target)
        assert target.read_bytes() == b"keep"
        assert await response.body.read() == peer.data


async def test_truncated_response_has_operation_context(peer, tmp_path):
    peer.truncate = True
    async with client(peer) as s3:
        response = await s3.get_object(bucket="test", key="source")
        with pytest.raises(RbotoConnectionError) as caught:
            await response.body.write_to(tmp_path / "partial")
        assert caught.value.operation_name == "GetObject"
        assert caught.value.response_metadata["RequestId"] == "file-test"
        assert await response.body.read() == b""


async def test_cancel_sink_then_reuse_client(peer, tmp_path):
    peer.pause = True
    async with client(peer) as s3:
        response = await s3.get_object(bucket="test", key="source")
        with anyio.move_on_after(.05) as scope:
            await response.body.write_to(tmp_path / "partial")
        assert scope.cancel_called
        await response.body.aclose()
        assert await response.body.read() == b""
        peer.resume.set()
        other = await s3.get_object(bucket="test", key="other")
        assert await other.body.read() == peer.data


async def test_explicit_stream_close_interrupts_sink(peer, tmp_path):
    peer.pause = True
    async with client(peer) as s3:
        response = await s3.get_object(bucket="test", key="source")
        async def close():
            await anyio.sleep(.05)
            await response.body.aclose()
        async with anyio.create_task_group() as group:
            group.start_soon(close)
            with pytest.raises(StreamClosedError):
                await response.body.write_to(tmp_path / "partial")


async def test_sink_outlives_client_and_consumes_remaining_body(peer, tmp_path):
    s3 = client(peer)
    response = await s3.get_object(bucket="test", key="source")
    first = await anext(response.body)
    await s3.aclose()
    target = tmp_path / "remaining"
    assert await response.body.write_to(target) == len(peer.data) - len(first)
    actual = hashlib.sha256(first + target.read_bytes()).digest()
    assert actual == hashlib.sha256(peer.data).digest()


async def test_upload_cancellation_keeps_client_usable(peer, tmp_path):
    source = tmp_path / "source"
    with source.open("wb") as output:
        for _ in range(32):
            output.write(peer.data)
    peer.pause = True
    async with client(peer) as s3:
        completed = False

        async def upload():
            nonlocal completed
            await s3.put_object(bucket="test", key="large", body=FileBody(source))
            completed = True

        async with anyio.create_task_group() as group:
            group.start_soon(upload)
            with anyio.fail_after(5):
                while not peer.started.is_set():
                    await anyio.sleep(.001)
            group.cancel_scope.cancel()
        assert not completed
        peer.resume.set()
        other = await s3.get_object(bucket="test", key="small")
        assert await other.body.read() == peer.data
