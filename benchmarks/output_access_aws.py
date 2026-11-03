"""Confirm SQS output access with real messages using two extracted release builds.

Requires the AWS CLI and credentials allowed to create/delete a temporary queue
and send/receive/delete its messages. Only this run's UUID queue is modified.
Request times are not a before/after network benchmark; timed work is on
already-received outputs. Payloads are ASCII, 4 KiB or 1 MiB.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path


async def measure(args):
    sys.path.insert(0, args.path)
    from rboto import ClientConfig, SQSClient, configure_runtime
    from rboto_sqs import _native

    configure_runtime(worker_threads=2)
    client = SQSClient(region=args.region, profile=args.profile,
                       config=ClientConfig(max_attempts=1, operation_timeout=30))
    payload = "a" * args.size
    expected_md5 = hashlib.md5(payload.encode(), usedforsecurity=False).hexdigest()
    sent = await client.send_message(queue_url=args.queue, message_body=payload)
    assert sent.md5_of_message_body == expected_md5
    response = await client.receive_message(
        queue_url=args.queue, max_number_of_messages=1, wait_time_seconds=20)
    start = time.perf_counter_ns()
    messages = response.messages
    first_us = (time.perf_counter_ns() - start) / 1000
    assert messages is not None and len(messages) == 1
    child = messages[0]
    assert child.message_id == sent.message_id
    assert child.body == payload and child.md5_of_body == expected_md5
    receipt = child.receipt_handle
    assert receipt is not None
    times = []
    cpu = time.process_time()
    for _ in range(1000):
        start = time.perf_counter_ns()
        value = response.messages
        times.append((time.perf_counter_ns() - start) / 1000)
        del value
    cpu = time.process_time() - cpu
    assert response.to_dict()["messages"][0]["body"] == payload
    del messages, response
    gc.collect()
    assert child.body == payload
    await client.delete_message(queue_url=args.queue, receipt_handle=receipt)
    print(json.dumps({
        "size": args.size, "first_us": first_us, "p50_us": statistics.median(times),
        "p95_us": sorted(times)[950], "getter_cpu_s": cpu, "failures": 0,
        "library_sha256": hashlib.sha256(Path(_native.__file__).read_bytes()).hexdigest(),
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-path")
    parser.add_argument("--after-path")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--path", help=argparse.SUPPRESS)
    parser.add_argument("--queue", help=argparse.SUPPRESS)
    parser.add_argument("--size", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.path:
        asyncio.run(measure(args))
        return
    if not all((args.before_path, args.after_path, args.output)):
        parser.error("--before-path, --after-path and --output are required")
    cli = ["aws", "--region", args.region]
    profile = ["--profile", args.profile] if args.profile else []
    cli += profile
    name = f"rboto-output-{uuid.uuid4().hex}"
    queue = subprocess.check_output([
        *cli, "sqs", "create-queue", "--queue-name", name, "--attributes",
        "MaximumMessageSize=1048576,MessageRetentionPeriod=60",
        "--query", "QueueUrl", "--output", "text",
    ], text=True).strip()
    time.sleep(1)
    report = {"region": args.region, "rows": [], "cleanup": False}
    schedule = [(label, path, size, r) for r in range(args.rounds)
                for size in (4096, 1024 * 1024)
                for label, path in (("before", args.before_path), ("after", args.after_path))]
    random.Random(58).shuffle(schedule)
    try:
        for label, path, size, round_id in schedule:
            result = subprocess.run(
                [sys.executable, __file__, "--path", path, "--queue", queue,
                 "--size", str(size), "--region", args.region, *profile],
                capture_output=True, text=True, check=False, timeout=90,
                env={**os.environ, "PYTHONUNBUFFERED": "1"})
            if result.returncode:
                report["failure"] = result.stderr
                raise RuntimeError(result.stderr)
            row = json.loads(result.stdout)
            row.update(label=label, round=round_id)
            report["rows"].append(row)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(f"{label} {size}: {row['p50_us']:.2f} us", flush=True)
    finally:
        subprocess.run([*cli, "sqs", "delete-queue", "--queue-url", queue], check=True)
        for _ in range(10):
            absent = subprocess.run(
                [*cli, "sqs", "get-queue-url", "--queue-name", name],
                capture_output=True, text=True, check=False,
            )
            report["cleanup"] = absent.returncode != 0 and "NonExistentQueue" in absent.stderr
            if report["cleanup"]:
                break
            time.sleep(1)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        if not report["cleanup"]:
            raise RuntimeError("Could not independently confirm queue deletion")


if __name__ == "__main__":
    main()
