"""Stream ownership and byte integrity through a compiled S3 client."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from rboto_s3 import S3Client

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(SELECTED and "s3" not in SELECTED), reason="S3 not selected"),
]


@dataclass
class Peer:
    endpoint: str = ""
    chunks: tuple[bytes, ...] = (b"first", b"second")
    pause_after_first: bool = False
    truncate: bool = False
    missing_key: bool = False
    failures: int = 0
    requests: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    first_sent: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Peer]:
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    state = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            state.requests += 1
            if state.missing_key or state.requests <= state.failures:
                body = b"<Error><Code>NoSuchKey</Code><Message>Missing object</Message></Error>"
                self.send_response(503 if state.requests <= state.failures else 404)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("x-amz-request-id", f"request-{state.requests}")
                self.send_header("x-amz-id-2", "extended-request")
                for name, value in state.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("x-amz-request-id", f"request-{state.requests}")
            self.send_header("x-amz-id-2", "extended-request")
            for name, value in state.headers.items():
                self.send_header(name, value)
            self.end_headers()
            try:
                for index, chunk in enumerate(state.chunks):
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                    if index == 0:
                        state.first_sent.set()
                        if state.pause_after_first and not state.resume.wait(10):
                            self.close_connection = True
                            return
                if state.truncate:
                    # Close before the final HTTP chunk: this is not a complete body.
                    self.close_connection = True
                else:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        state.resume.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def client_for(peer: Peer, *, max_attempts: int = 1) -> S3Client:
    from rboto import ClientConfig, S3Client

    return S3Client(
        region="us-east-1",
        endpoint_url=peer.endpoint,
        config=ClientConfig(
            access_key_id="testing",
            secret_access_key="testing",
            max_attempts=max_attempts,
            read_timeout=5,
        ),
    )


@pytest.mark.parametrize(
    "chunks",
    [
        (),
        (b"single chunk",),
        (bytes(range(256)),),
        (bytes(range(256)) * 128,) * 256,
        (b"\x00", b"\xff", b"binary\x00\x80", b"tail"),
    ],
    ids=["empty", "single", "all-byte-values", "8-mib", "multiple"],
)
async def test_read_preserves_bytes_and_exhausts_stream(
    peer: Peer, chunks: tuple[bytes, ...]
) -> None:
    peer.chunks = chunks
    response = await client_for(peer).get_object(bucket="bucket", key="key")
    body = response.body
    result = await body.read()
    assert type(result) is bytes
    assert result == b"".join(chunks)
    assert await body.read() == b""
    assert [chunk async for chunk in body] == []


async def test_iteration_then_read_returns_only_remaining_bytes(peer: Peer) -> None:
    peer.pause_after_first = True
    response = await client_for(peer).get_object(bucket="bucket", key="key")
    first = await asyncio.wait_for(anext(response.body), timeout=3)
    assert first == peer.chunks[0]
    peer.resume.set()
    assert await response.body.read() == b"".join(peer.chunks[1:])
    assert await response.body.read() == b""


async def test_iteration_still_preserves_complete_body(peer: Peer) -> None:
    peer.chunks = (bytes(range(256)) * 128,) * 16
    response = await client_for(peer).get_object(bucket="bucket", key="key")
    assert b"".join([chunk async for chunk in response.body]) == b"".join(peer.chunks)
    assert await response.body.read() == b""


async def test_competing_reads_consume_body_once_without_blocking_loop(peer: Peer) -> None:
    peer.pause_after_first = True
    response = await client_for(peer).get_object(bucket="bucket", key="key")
    reads = [asyncio.ensure_future(response.body.read()) for _ in range(2)]
    try:
        done, pending = await asyncio.wait(reads, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        # One reader owns the body and is waiting for the server. The other sees
        # an exhausted stream, and the Python event loop can still run.
        assert len(done) == len(pending) == 1
        assert done.pop().result() == b""
        assert await asyncio.to_thread(peer.first_sent.wait, 3)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body)
        peer.resume.set()
        assert await asyncio.wait_for(pending.pop(), timeout=3) == b"".join(peer.chunks)
    finally:
        peer.resume.set()
        for task in reads:
            task.cancel()
        await asyncio.gather(*reads, return_exceptions=True)


async def test_cancelling_pending_read_keeps_stream_consumed(peer: Peer) -> None:
    peer.pause_after_first = True
    response = await client_for(peer).get_object(bucket="bucket", key="key")
    reads = [asyncio.ensure_future(response.body.read()) for _ in range(2)]
    try:
        done, pending = await asyncio.wait(reads, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == len(pending) == 1
        assert done.pop().result() == b""
        read = pending.pop()
        assert await asyncio.to_thread(peer.first_sent.wait, 3)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(response.body), timeout=3)
        assert not read.done()
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        assert await response.body.read() == b""
    finally:
        peer.resume.set()
        for task in reads:
            task.cancel()
        await asyncio.gather(*reads, return_exceptions=True)


@pytest.mark.parametrize("collect", [False, True])
async def test_interrupted_body_raises_and_cannot_be_reused(peer: Peer, collect: bool) -> None:
    from rboto.exceptions import ConnectionError

    peer.truncate = True
    peer.failures = 1
    peer.headers = {"x-private-data": "private header", "Set-Cookie": "private cookie"}
    async with client_for(peer, max_attempts=2) as client:
        response = await client.get_object(bucket="bucket", key="key")
    # Context must belong to the response, even after its client is gone.
    with pytest.raises(ConnectionError) as caught:
        if collect:
            await response.body.read()
        else:
            _ = [chunk async for chunk in response.body]
    error = caught.value
    assert error.operation_name == "GetObject"
    assert error.request_id == "request-2"
    assert error.response_metadata == {
        "HTTPStatusCode": 200, "RequestId": "request-2",
        "HostId": "extended-request", "RetryAttempts": 1,
    }
    assert error.response["ResponseMetadata"] == error.response_metadata
    assert not error.details
    assert "private" not in str(error) + repr(error.response)
    assert peer.requests == 2  # Reading a stream never dispatches a retry.
    assert await response.body.read() == b""
    with pytest.raises(StopAsyncIteration):
        await anext(response.body)


async def test_missing_object_keeps_modeled_exception(peer: Peer) -> None:
    from rboto_s3.exceptions import NoSuchKeyError

    peer.missing_key = True
    with pytest.raises(NoSuchKeyError) as caught:
        await client_for(peer).get_object(bucket="bucket", key="missing")
    assert caught.value.response_metadata["HTTPStatusCode"] == 404
    assert caught.value.response_metadata["RetryAttempts"] == 0


@pytest.mark.parametrize("collect", [False, True])
async def test_checksum_failure_is_a_bounded_response_error(peer: Peer, collect: bool) -> None:
    from rboto.exceptions import ResponseError

    # CRC32 of empty bytes, deliberately wrong for this nonempty response.
    peer.headers = {"x-amz-checksum-crc32": "AAAAAA=="}
    response = await client_for(peer).get_object(
        bucket="bucket", key="key", checksum_mode="ENABLED",
    )
    with pytest.raises(ResponseError) as caught:
        if collect:
            await response.body.read()
        else:
            _ = [chunk async for chunk in response.body]
    assert caught.value.operation_name == "GetObject"
    assert caught.value.response_metadata["HTTPStatusCode"] == 200
    assert caught.value.request_id == "request-1"
    assert "AAAA" not in str(caught.value)
    assert await response.body.read() == b""
