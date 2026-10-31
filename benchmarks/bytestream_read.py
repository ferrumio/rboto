"""Compare two release builds against real S3 in fresh, alternating processes.

Install boto3, psutil, rboto, and one rboto-s3 wheel in the runner environment.
Extract each rboto-s3 wheel into its own directory, then run:

    python benchmarks/bytestream_read.py --before-path /path/to/before \
        --after-path /path/to/after --output /path/to/results.json

Uses the default credential chain. Creates one private UUID bucket and deletes
it in finally. Credentials are passed to child processes in memory only.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import gc
import hashlib
import json
import multiprocessing
import os
import platform
import random
import sys
import threading
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

MIB = 1024 * 1024


def allocator_snapshot():
    """Linux/glibc diagnostic; no allocator tuning or malloc_trim."""
    if platform.libc_ver()[0] != "glibc":
        return None
    libc = ctypes.CDLL(None)
    libc.open_memstream.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    libc.open_memstream.restype = ctypes.c_void_p
    libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.fflush.argtypes = [ctypes.c_void_p]
    libc.fclose.argtypes = [ctypes.c_void_p]
    libc.free.argtypes = [ctypes.c_void_p]
    pointer = ctypes.c_void_p()
    length = ctypes.c_size_t()
    stream = libc.open_memstream(ctypes.byref(pointer), ctypes.byref(length))
    if not stream:
        raise RuntimeError("open_memstream failed")
    try:
        if libc.malloc_info(0, stream) != 0:
            raise RuntimeError("malloc_info failed")
        libc.fflush(stream)
        xml = ctypes.string_at(pointer.value, length.value)
    finally:
        libc.fclose(stream)
        libc.free(pointer)
    root = ET.fromstring(xml)
    system = int(root.find("system[@type='current']").attrib["size"])
    free = sum(int(root.find(f"total[@type='{kind}']").attrib["size"]) for kind in ("rest", "fast"))
    return {
        "arena_system_bytes": system,
        "arena_free_bytes": free,
        "arena_nonfree_bytes": system - free,
        "mmap_bytes": int(root.find("total[@type='mmap']").attrib["size"]),
    }


def snapshot():
    import psutil

    process = psutil.Process()
    memory = process.memory_full_info()
    return {
        "rss": memory.rss,
        "uss": memory.uss,
        "threads": process.num_threads(),
        "allocator": allocator_snapshot(),
    }


async def measure(connection, settings, credentials):
    # Import only this build, before importing rboto's lazy client.
    sys.path.insert(0, settings["package_path"])
    from rboto import ClientConfig, S3Client
    from rboto_s3 import _native

    native_path = Path(_native.__file__).resolve()
    if not native_path.is_relative_to(Path(settings["package_path"]).resolve()):
        raise RuntimeError(f"Imported wrong build: {native_path}")
    with native_path.open("rb") as native_file:
        native_sha256 = hashlib.file_digest(native_file, "sha256").hexdigest()
    connection.send(
        {
            "native_path": str(native_path),
            "native_sha256": native_sha256,
            "python": sys.version,
        }
    )
    client = S3Client(
        region=settings["region"],
        config=ClientConfig(
            access_key_id=credentials["access_key"],
            secret_access_key=credentials["secret_key"],
            session_token=credentials["token"],
            max_attempts=3,
            retry_mode="standard",
            connect_timeout=10,
            read_timeout=30,
            operation_timeout=60,
        ),
        interceptors=[],
    )
    size = settings["size"]

    async def download():
        response = await client.get_object(
            bucket=settings["bucket"],
            key=f"fixture/{size}",
            checksum_mode="ENABLED",
        )
        return await response.body.read()

    async def batch(count):
        if settings["concurrency"] == 1:
            return [await download() for _ in range(count)]
        results = await asyncio.gather(*(download() for _ in range(count)), return_exceptions=True)
        failures = [item for item in results if isinstance(item, BaseException)]
        if failures:
            raise RuntimeError(f"Download failures: {failures!r}")
        return results

    def verify(bodies):
        for body in bodies:
            if len(body) != size or hashlib.sha256(body).hexdigest() != settings["sha256"]:
                raise AssertionError("Body length or SHA-256 mismatch")

    warmup = await batch(settings["concurrency"])
    verify(warmup)
    del warmup
    await asyncio.sleep(0)
    gc.collect()
    before = snapshot()
    connection.send({"stage": "ready"})
    if connection.recv() != "start":
        raise RuntimeError("Expected start command")
    cpu = time.process_time()
    started = time.perf_counter()
    bodies = await batch(12)
    wall = time.perf_counter() - started
    cpu = time.process_time() - cpu
    held = snapshot()
    # Parent sampling stops before SHA-256 checks and post-release diagnostics.
    connection.send({"stage": "downloaded"})
    verify(bodies)
    del bodies
    await asyncio.sleep(0.05)
    gc.collect()
    connection.send(
        {
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            "verified": 12,
            "warmup_verified": settings["concurrency"],
            "before": before,
            "held": held,
            "released": snapshot(),
        }
    )


def worker(connection, settings, credentials):
    try:
        asyncio.run(measure(connection, settings, credentials))
    except BaseException:
        connection.send({"fatal": traceback.format_exc()})
    finally:
        connection.close()


def receive(connection):
    if not connection.poll(240):
        raise TimeoutError("Benchmark worker")
    message = connection.recv()
    if "fatal" in message:
        raise RuntimeError(message["fatal"])
    return message


def run_case(settings, credentials):
    import psutil

    context = multiprocessing.get_context("spawn")
    connection, child = context.Pipe()
    process = context.Process(target=worker, args=(child, settings, credentials))
    process.start()
    child.close()
    stop = threading.Event()
    samples = []
    sampler = None
    try:
        identity = receive(connection)
        assert receive(connection) == {"stage": "ready"}
        ps = psutil.Process(process.pid)

        def sample():
            while not stop.wait(0.001):
                samples.append(ps.memory_info().rss)

        sampler = threading.Thread(target=sample)
        sampler.start()
        connection.send("start")
        assert receive(connection) == {"stage": "downloaded"}
        stop.set()
        sampler.join()
        result = receive(connection)
        result["sampled_peak_rss"] = max([result["before"]["rss"], result["held"]["rss"], *samples])
        return {**identity, **result}
    finally:
        stop.set()
        if sampler is not None:
            sampler.join()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
        connection.close()


def main():
    import boto3

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-path", type=Path, required=True)
    parser.add_argument("--after-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("Use at least three rounds")
    builds = {"before": args.before_path.resolve(), "after": args.after_path.resolve()}
    for directory in builds.values():
        if not (directory / "rboto_s3").is_dir():
            parser.error(f"No rboto_s3 package in {directory}")
    frozen = boto3.Session().get_credentials().get_frozen_credentials()
    credentials = {
        "access_key": frozen.access_key,
        "secret_key": frozen.secret_key,
        "token": frozen.token,
    }
    s3 = boto3.client(
        "s3",
        region_name=args.region,
        aws_access_key_id=frozen.access_key,
        aws_secret_access_key=frozen.secret_key,
        aws_session_token=frozen.token,
    )
    bucket = f"rboto-read-{uuid.uuid4().hex}"
    sizes = [MIB, 8 * MIB]
    report = {
        "bucket": bucket,
        "region": args.region,
        "host": platform.platform(),
        "cpu_count": os.cpu_count(),
        "tokio_worker_threads": os.environ.get("TOKIO_WORKER_THREADS"),
        "rounds": args.rounds,
        "status": "running",
        "cases": [],
    }
    created = False

    def save():
        args.output.write_text(json.dumps(report, indent=2) + "\n")

    try:
        options = {}
        if args.region != "us-east-1":
            options["CreateBucketConfiguration"] = {"LocationConstraint": args.region}
        s3.create_bucket(Bucket=bucket, **options)
        created = True
        checksums = {}
        for size in sizes:
            body = random.Random(817 + size).randbytes(size)
            checksums[size] = hashlib.sha256(body).hexdigest()
            s3.put_object(
                Bucket=bucket, Key=f"fixture/{size}", Body=body, ChecksumAlgorithm="CRC32"
            )
        del body
        for round_number in range(args.rounds):
            order = list(builds) if round_number % 2 == 0 else list(reversed(builds))
            for size in sizes:
                for concurrency in [1, 12]:
                    for build in order:
                        settings = {
                            "package_path": str(builds[build]),
                            "region": args.region,
                            "bucket": bucket,
                            "size": size,
                            "sha256": checksums[size],
                            "concurrency": concurrency,
                        }
                        result = run_case(settings, credentials)
                        case = {
                            "build": build,
                            "round": round_number,
                            "size": size,
                            "concurrency": concurrency,
                            **result,
                        }
                        report["cases"].append(case)
                        save()
                        print(json.dumps(case), flush=True)
        report["status"] = "completed"
    except BaseException:
        report["status"] = "failed"
        report["fatal"] = traceback.format_exc()
        raise
    finally:
        try:
            if created:
                result = s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": f"fixture/{size}"} for size in sizes]},
                )
                if result.get("Errors"):
                    raise RuntimeError(f"Fixture cleanup failed: {result['Errors']}")
                s3.delete_bucket(Bucket=bucket)
                report["cleanup"] = {"bucket_deleted": True, "objects_deleted": len(sizes)}
        except BaseException:
            report["status"] = "failed"
            report["cleanup_error"] = traceback.format_exc()
            raise
        finally:
            save()
            s3.close()


if __name__ == "__main__":
    main()
