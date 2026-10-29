"""Measure local operation middleware; run each mode in a fresh process."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import resource
import statistics
import time
import tracemalloc
from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING

from rboto import DynamoDBClient

if TYPE_CHECKING:
    from rboto import Interceptor, LogLevel, Next, OperationRequest, OperationResponse

RESPONSE_BODY = b'{"TableNames":["example"]}'
RESPONSE = (
    b"HTTP/1.1 200 OK\r\nContent-Type: application/x-amz-json-1.0\r\n"
    b"x-amzn-requestid: benchmark-request\r\nContent-Length: "
    + str(len(RESPONSE_BODY)).encode()
    + b"\r\n\r\n"
    + RESPONSE_BODY
)


async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            headers = await reader.readuntil(b"\r\n\r\n")
            for line in headers.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    await reader.readexactly(int(line.split(b":", 1)[1]))
            writer.write(RESPONSE)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def middleware(request: OperationRequest, call_next: Next) -> OperationResponse:
    return await call_next(request)


class MemorySink(io.StringIO):
    """Count JSON lines and retain a bounded sample without external I/O."""

    def __init__(self) -> None:
        super().__init__()
        self.records: deque[str] = deque(maxlen=256)
        self.count = 0

    def write(self, value: str) -> int:
        self.count += value.count("\n")
        self.records.append(value)
        return len(value)


async def run(mode: str, count: int, concurrency: int, allocations: bool) -> dict[str, object]:
    for name in tuple(os.environ):
        if name.startswith("AWS_"):
            del os.environ[name]
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_MAX_ATTEMPTS="1",
    )
    connections: set[asyncio.StreamWriter] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.add(writer)
        try:
            await peer(reader, writer)
        finally:
            connections.discard(writer)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    interceptors: Sequence[Interceptor] = (middleware,) if mode == "middleware" else ()
    sink: MemorySink | None = None
    if mode.startswith("logging-"):
        from rboto import configure_logging

        sink = MemorySink()
        level: LogLevel = (
            "OFF" if mode == "logging-disabled" else "INFO" if mode == "logging-info" else "DEBUG"
        )
        configure_logging(level=level, stream=sink)
    # Omitting the new keyword allows --mode baseline to run against the parent revision.
    client = (
        DynamoDBClient(region="us-east-1", endpoint_url=f"http://127.0.0.1:{port}")
        if mode == "baseline"
        else DynamoDBClient(
            region="us-east-1",
            endpoint_url=f"http://127.0.0.1:{port}",
            interceptors=interceptors,
        )
    )
    latencies: list[float] = []

    async def worker(requests: int, record: bool) -> None:
        for _ in range(requests):
            started = time.perf_counter()
            result = await client.list_tables()
            elapsed = time.perf_counter() - started
            assert result.table_names == ["example"]
            if record:
                latencies.append(elapsed * 1_000_000)

    async with server:
        try:
            await asyncio.gather(*(worker(20, False) for _ in range(concurrency)))
            if sink is not None:
                sink.records.clear()
                sink.count = 0
            if allocations:
                tracemalloc.start()
            cpu_start = time.process_time()
            wall_start = time.perf_counter()
            await asyncio.gather(
                *(
                    worker(count // concurrency + (i < count % concurrency), True)
                    for i in range(concurrency)
                )
            )
            wall = time.perf_counter() - wall_start
            cpu = time.process_time() - cpu_start
            peak_python_bytes = tracemalloc.get_traced_memory()[1] if allocations else None
            if allocations:
                tracemalloc.stop()
        finally:
            # The native client pools connections. Close the peer's accepted
            # sockets before Server.wait_closed(), outside the measured interval.
            for writer in tuple(connections):
                writer.close()
            await asyncio.gather(*(writer.wait_closed() for writer in tuple(connections)))
            if sink is not None:
                configure_logging(level="OFF")
                sink.close()
    ordered = sorted(latencies)
    return {
        "mode": mode,
        "requests": count,
        "concurrency": concurrency,
        "wall_seconds": wall,
        "process_cpu_seconds": cpu,
        "requests_per_second": count / wall,
        "p50_us": statistics.median(ordered),
        "p95_us": ordered[int((len(ordered) - 1) * 0.95)],
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "peak_python_traced_bytes": peak_python_bytes,
        "log_records": sink.count if sink is not None else 0,
        "note": "Includes the local HTTP peer; traced allocations exclude native memory.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=[
            "baseline",
            "disabled",
            "middleware",
            "logging-disabled",
            "logging-info",
            "logging-debug",
        ],
        required=True,
    )
    parser.add_argument("--requests", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--allocations", action="store_true")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("requests and concurrency must be positive")
    print(
        json.dumps(asyncio.run(run(args.mode, args.requests, args.concurrency, args.allocations)))
    )
