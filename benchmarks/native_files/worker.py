"""One isolated benchmark sample; same compiled client for both file paths."""
import argparse
import gc
import hashlib
import json
import os
import resource
import threading
import time
import uuid
from pathlib import Path

import anyio
import psutil
from rboto import ClientConfig, FileBody, S3Client


def isolate_environment():
    # Keep real credentials and HTTP proxies out of this local experiment.
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(name, None)
    os.environ.update(
        AWS_EC2_METADATA_DISABLED="true",
        AWS_CONFIG_FILE="/nonexistent/rboto-file-benchmark",
        AWS_SHARED_CREDENTIALS_FILE="/nonexistent/rboto-file-benchmark",
        AWS_REQUEST_CHECKSUM_CALCULATION="WHEN_REQUIRED",
        AWS_RESPONSE_CHECKSUM_VALIDATION="WHEN_REQUIRED",
    )


def client(endpoint, attempts=1):
    return S3Client(
        endpoint_url=endpoint,
        region="eu-west-2",
        config=ClientConfig(
            access_key_id="testing", secret_access_key="testing",
            max_attempts=attempts, connect_timeout=5, read_timeout=30,
        ),
    )


async def chunks(path, size, counters):
    async with await anyio.open_file(path, "rb") as stream:
        while chunk := await stream.read(size):
            counters["python_chunks"] += 1
            yield chunk


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def transfer(s3, args, index, prefix):
    key = f"{args.size}-{prefix}-{index}"
    counters = {"python_chunks": 0, "python_write_calls": 0}
    if args.direction == "upload":
        body = (
            FileBody(Path(args.source), buffer_size=args.chunk)
            if args.mode == "native" else chunks(args.source, args.chunk, counters)
        )
        if args.mode == "bytes":
            body = args.bytes_body
        await s3.put_object(
            bucket="benchmark", key=key, body=body, content_length=args.size,
        )
        return {"key": key, **counters}
    response = await s3.get_object(bucket="benchmark", key=key)
    target = Path(args.directory) / f"{prefix}-{index}.bin"
    async with response.body:
        if args.mode == "native":
            written = await response.body.write_to(target)
        else:
            written = 0
            # A buffered file, same 1 MiB write buffer as the native sink.
            async with await anyio.open_file(target, "xb", buffering=1024 * 1024) as output:
                pending = bytearray()
                async for chunk in response.body:
                    counters["python_chunks"] += 1
                    if args.mode == "python-batched":
                        pending.extend(chunk)
                        if len(pending) >= args.chunk:
                            written += await output.write(pending)
                            counters["python_write_calls"] += 1
                            pending.clear()
                    else:
                        written += await output.write(chunk)
                        counters["python_write_calls"] += 1
                if pending:
                    written += await output.write(pending)
                    counters["python_write_calls"] += 1
    if written != args.size:
        raise AssertionError((written, args.size))
    return {"key": key, "path": str(target), "written": written, **counters}


async def sample(args):
    # The bytes control shares one preloaded immutable payload across uploads.
    # Loading is excluded from timing, but its memory remains in absolute RSS.
    args.bytes_body = Path(args.source).read_bytes() if args.mode == "bytes" else None
    s3 = client(args.endpoint)
    warm = await s3.get_object(bucket="benchmark", key=f"65536-warm-{uuid.uuid4().hex}")
    await warm.body.read()
    # Initialize each baseline file/thread bridge before measuring.
    async with await anyio.open_file(args.source, "rb") as stream:
        await stream.read(1)
    gc.collect()
    process = psutil.Process()
    rss_start = process.memory_info().rss
    rss_samples = [rss_start]
    stop = threading.Event()

    def sample_rss():
        while not stop.wait(0.005):
            rss_samples.append(process.memory_info().rss)

    thread = threading.Thread(target=sample_rss)
    thread.start()
    gaps = []
    done = anyio.Event()

    async def heartbeat():
        previous = time.perf_counter()
        while not done.is_set():
            await anyio.sleep(0.001)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    results = []
    prefix = uuid.uuid4().hex

    async def one(index):
        results.append(await transfer(s3, args, index, prefix))

    before = resource.getrusage(resource.RUSAGE_SELF)
    async with anyio.create_task_group() as monitor:
        monitor.start_soon(heartbeat)
        await anyio.sleep(0)
        start = time.perf_counter()
        async with anyio.create_task_group() as jobs:
            for index in range(args.concurrency):
                jobs.start_soon(one, index)
        elapsed = time.perf_counter() - start
        done.set()
    after = resource.getrusage(resource.RUSAGE_SELF)
    stop.set()
    thread.join()
    await s3.aclose()
    for result in results:
        if "path" in result:
            result["sha256"] = digest_file(result["path"])
            if result["sha256"] != args.sha256:
                raise AssertionError(result)
            Path(result.pop("path")).unlink()
    ordered = sorted(gaps)
    return {
        "mode": args.mode, "direction": args.direction, "backend": args.backend,
        "size": args.size, "concurrency": args.concurrency, "chunk": args.chunk,
        "wall_s": elapsed,
        "cpu_s": after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
        "user_s": after.ru_utime - before.ru_utime,
        "system_s": after.ru_stime - before.ru_stime,
        "rss_start": rss_start, "rss_peak": max(rss_samples),
        "rss_growth": max(rss_samples) - rss_start,
        "loop_gap_max_ms": max(gaps, default=0) * 1000,
        "loop_gap_p99_ms": (
            ordered[min(len(ordered) - 1, int(len(ordered) * .99))] * 1000 if ordered else 0
        ),
        "loop_samples": len(gaps), "results": results,
    }


if __name__ == "__main__":
    isolate_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--chunk", type=int, default=1024 * 1024)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--mode", choices=("bytes", "python", "python-batched", "native"), required=True,
    )
    parser.add_argument("--direction", choices=("upload", "download"), required=True)
    parser.add_argument("--backend", choices=("asyncio", "trio"), default="asyncio")
    args = parser.parse_args()
    print(json.dumps(anyio.run(sample, args, backend=args.backend)))
