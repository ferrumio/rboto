"""Compare runtime workers using compiled clients and a separate local HTTP peer.

Requires release rboto-s3/rboto-sqs wheels and psutil. No account access.
Run: python benchmarks/runtime_workers.py --rounds 5 --output /tmp/runtime-workers.json
Each batch runs in a fresh process; the peer's CPU and RSS are excluded.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib
import json
import multiprocessing
import os
import platform
import random
import statistics
import time
import traceback
import zlib
from pathlib import Path

MIB = 1024 * 1024
CASES = [
    ("s3", 4096, 1), ("s3", MIB, 1), ("s3", MIB, 16), ("s3", 8 * MIB, 16),
    ("sqs", 4096, 1), ("sqs", 4096, 16), ("sqs", MIB, 1), ("sqs", MIB, 16),
    ("mixed", MIB, 16),
]


def peer_worker(connection, delay):
    async def serve():
        payloads = {size: b"a" * size for size in (4096, MIB, 8 * MIB)}
        crcs = {size: base64.b64encode(zlib.crc32(body).to_bytes(4, "big"))
                for size, body in payloads.items()}
        sqs = {size: json.dumps({"MessageId": "benchmark",
               "MD5OfMessageBody": hashlib.md5(body, usedforsecurity=False).hexdigest()}).encode()
               for size, body in payloads.items()}

        async def handle(reader, writer):
            try:
                while True:
                    raw = await reader.readuntil(b"\r\n\r\n")
                    lines = raw.decode().split("\r\n")
                    headers = dict(line.lower().split(":", 1) for line in lines[1:] if ":" in line)
                    method, target, _ = lines[0].split(" ")
                    request = await reader.readexactly(int(headers.get("content-length", "0")))
                    if method == "GET":
                        size = int(target.split("?", 1)[0].rsplit("/", 1)[-1])
                        body = payloads[size]
                        extra = b"content-type: application/octet-stream\r\nx-amz-checksum-crc32: "
                        extra += crcs[size] + b"\r\n"
                    else:
                        message = json.loads(request)["MessageBody"]
                        assert message == "a" * len(message)
                        body = sqs[len(message)]
                        extra = b"content-type: application/x-amz-json-1.0\r\n"
                    if delay:
                        await asyncio.sleep(delay)
                    writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: " + str(len(body)).encode()
                                 + b"\r\n" + extra + b"\r\n")
                    writer.write(body)
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        connection.send(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        async with server:
            await server.serve_forever()

    asyncio.run(serve())


def snapshot():
    import psutil

    process = psutil.Process()
    return {"rss": process.memory_info().rss, "threads": process.num_threads()}


def percentile(values, quantile):
    return sorted(values)[min(len(values) - 1, int(len(values) * quantile))]


async def measure(connection, endpoint, case, workers, operations):
    before_import = snapshot()
    from rboto import ClientConfig, S3Client, SQSClient, configure_runtime
    from rboto_s3 import _native as native_s3
    from rboto_sqs import _native as native_sqs

    configure_runtime(worker_threads=workers)
    kind, size, concurrency = case
    config = ClientConfig(access_key_id="testing", secret_access_key="testing", max_attempts=1,
                          connect_timeout=10, operation_timeout=30)
    settings = {"region": "us-east-1", "endpoint_url": endpoint, "config": config}
    s3 = S3Client(**settings) if kind in {"s3", "mixed"} else None
    sqs = SQSClient(**settings) if kind in {"sqs", "mixed"} else None
    after_construction = snapshot()
    assert native_s3._runtime_worker_threads() is None
    assert native_sqs._runtime_worker_threads() is None
    payload = b"a" * size
    message = payload.decode()
    md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    semaphore = asyncio.Semaphore(concurrency)

    async def operation(index):
        async with semaphore:
            started = time.perf_counter()
            if s3 is not None and (sqs is None or index % 2 == 0):
                response = await s3.get_object(bucket="benchmark", key=str(size),
                                               checksum_mode="ENABLED")
                body = await response.body.read()
                assert body == payload
            else:
                response = await sqs.send_message(
                    queue_url=endpoint + "/queue", message_body=message,
                )
                assert response.md5_of_message_body == md5
            return time.perf_counter() - started

    await asyncio.gather(*(operation(index) for index in range(max(2, concurrency))))
    before = snapshot()
    connection.send({"stage": "ready", "pid": os.getpid()})
    if connection.recv() != "start":
        raise RuntimeError("Expected start")
    gaps = []
    done = False

    async def tick():
        previous = time.perf_counter()
        while not done:
            await asyncio.sleep(0.001)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0)
    cpu = time.process_time()
    started = time.perf_counter()
    latencies = await asyncio.gather(*(operation(index) for index in range(operations)))
    wall = time.perf_counter() - started
    cpu = time.process_time() - cpu
    done = True
    await ticker
    connection.send({
        "stage": "result", "wall_s": wall, "cpu_s": cpu, "ops_s": operations / wall,
        "latency_p50_ms": statistics.median(latencies) * 1000,
        "latency_p95_ms": percentile(latencies, 0.95) * 1000,
        "loop_max_gap_ms": max(gaps, default=0) * 1000,
        "before_import": before_import, "after_construction": after_construction,
        "before": before, "after": snapshot(), "verified": operations,
        "runtime_workers": {"s3": native_s3._runtime_worker_threads(),
                            "sqs": native_sqs._runtime_worker_threads()},
    })


def worker(connection, endpoint, case, workers, operations):
    try:
        for name in tuple(os.environ):
            if (name.startswith("AWS_")
                    or name.lower() in {"http_proxy", "https_proxy", "all_proxy"}):
                os.environ.pop(name)
        os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
        os.environ["AWS_CONFIG_FILE"] = os.devnull
        os.environ["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
        asyncio.run(measure(connection, endpoint, case, workers, operations))
    except BaseException:
        connection.send({"stage": "error", "traceback": traceback.format_exc()})
        raise
    finally:
        connection.close()


def run_batch(context, endpoint, case, workers, operations):
    import psutil

    parent, child = context.Pipe()
    process = context.Process(target=worker, args=(child, endpoint, case, workers, operations))
    process.start()
    child.close()
    try:
        if not parent.poll(60):
            raise TimeoutError("Worker did not warm up")
        ready = parent.recv()
        if ready["stage"] != "ready":
            raise RuntimeError(ready)
        sampled = psutil.Process(process.pid)
        peak_rss = 0
        peak_threads = 0
        parent.send("start")
        deadline = time.monotonic() + 60
        while not parent.poll(0.002):
            peak_rss = max(peak_rss, sampled.memory_info().rss)
            peak_threads = max(peak_threads, sampled.num_threads())
            if time.monotonic() > deadline:
                raise TimeoutError("Timed batch did not finish")
        result = parent.recv()
        if result["stage"] != "result":
            raise RuntimeError(result)
        result.update({"peak_rss": max(peak_rss, result["after"]["rss"]),
                       "peak_threads": max(peak_threads, result["after"]["threads"])})
        process.join(10)
        assert process.exitcode == 0
        return result
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        parent.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--operations", type=int, default=64)
    available = (
        len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    )
    parser.add_argument("--baseline-workers", type=int, default=available)
    parser.add_argument("--peer-delay", type=float, default=0.002)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 3 or args.operations < 16 or args.baseline_workers < 1:
        parser.error("Use at least 3 rounds, 16 operations and a positive baseline worker count")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    peer = context.Process(target=peer_worker, args=(child, args.peer_delay), daemon=True)
    peer.start()
    child.close()
    rows = []
    builds = {}
    for name in ("s3", "sqs"):
        native = importlib.import_module(f"rboto_{name}._native")
        path = Path(native.__file__)
        builds[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    settings = sorted({1, 2, 4, args.baseline_workers})
    plan = [(case, workers, round_) for round_ in range(args.rounds)
            for case in CASES for workers in settings]
    random.Random(53).shuffle(plan)
    try:
        if not parent.poll(15):
            raise TimeoutError("Peer did not start")
        endpoint = parent.recv()
        for index, (case, workers, round_) in enumerate(plan, 1):
            row = {"service": case[0], "size": case[1], "concurrency": case[2],
                   "workers": workers, "round": round_}
            row.update(run_batch(context, endpoint, case, workers, args.operations))
            rows.append(row)
            args.output.write_text(json.dumps({
                "platform": platform.platform(), "python": platform.python_version(),
                "settings": vars(args) | {"output": str(args.output)},
                "builds": builds, "rows": rows,
            }, indent=2) + "\n")
            print(
                f"{index}/{len(plan)} {case} workers={workers}: {row['ops_s']:.1f} ops/s",
                flush=True,
            )
    finally:
        peer.terminate()
        peer.join(10)
        parent.close()


if __name__ == "__main__":
    main()
