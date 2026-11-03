"""Measure conversion and output access in release wheels against a separate peer.

Extract each build's wheels into a directory, then run:
python benchmarks/output_access.py --before-path /tmp/before --after-path /tmp/after \
    --rounds 5 --output /tmp/output-access.json

No credentials or account access. Every case runs in a fresh process. Request
timing, synchronous native entry, and already-fetched property access are separate.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import importlib
import io
import json
import multiprocessing
import os
import platform
import random
import statistics
import sys
import time
import traceback
from pathlib import Path

MIB = 1024 * 1024
# Local stress shapes can exceed a service's response limit; no such request is
# sent to a real service. "wide" distinguishes allocation cost from payload size.
CASES = [
    ("sqs", "small", "getters"), ("sqs", "large", "getters"),
    ("sqs", "wide", "getters"), ("sqs", "large", "dict"),
    ("sqs", "small", "request"), ("sqs", "large", "request"),
    ("sqs", "wide", "request"), ("sqs", "small", "parallel"),
    ("sqs", "large", "consume"), ("sqs", "wide", "consume"),
    ("sqs", "large", "parallel"), ("sqs", "small", "entry"),
    ("sqs", "large", "entry"), ("dynamodb", "small", "getters"),
    ("dynamodb", "large", "getters"), ("dynamodb", "large", "entry"),
    ("lambda", "large", "entry"), ("lambda", "large", "getters"),
    ("s3", "small", "getters"), ("s3", "wide", "getters"),
    ("s3", "wide", "request"),
    ("s3", "wide", "consume"),
    ("s3", "large", "read"), ("s3", "large", "chunks"),
    ("sqs", "small", "logging-off"), ("sqs", "small", "logging"),
    ("sqs", "small", "logging-slow"),
    ("sqs", "small", "idle"), ("sqs", "small", "blocking"),
]


def shape(size, service="sqs"):
    if size == "wide":
        return 1000, 128
    large = 64 * 1024 if service == "dynamodb" else MIB
    return 10, large if size == "large" else 128


def fixtures():
    result = {}
    for size in ("small", "large", "wide"):
        count, length = shape(size)
        messages = [{"MessageId": str(i), "Body": "a" * length,
                     "MessageAttributes": {"tag": {"DataType": "String", "StringValue": "value"}},
                     "Attributes": {"SentTimestamp": "1"}} for i in range(count)]
        result[("sqs", size)] = json.dumps({"Messages": messages}).encode()
        count, length = shape(size, "dynamodb")
        items = [{"pk": {"S": str(i)}, "data": {"M": {
            "text": {"S": "a" * length}, "flags": {"L": [{"BOOL": True}, {"N": "1"}]}}}}
                 for i in range(count)]
        result[("dynamodb", size)] = json.dumps({"Items": items, "Count": count}).encode()
        result[("s3", size)] = (
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            + "".join(f"<Contents><Key>{i}</Key><Size>128</Size><Owner>"
                      "<ID>owner</ID><DisplayName>name</DisplayName></Owner></Contents>"
                      for i in range(count)) + "</ListBucketResult>").encode()
    return result


def peer_worker(connection):
    async def serve():
        bodies = fixtures()
        attempts = connections = 0

        async def handle(reader, writer):
            nonlocal attempts, connections
            counted = False
            try:
                while True:
                    raw = await reader.readuntil(b"\r\n\r\n")
                    lines = raw.decode().split("\r\n")
                    headers = dict(line.lower().split(":", 1)
                                   for line in lines[1:] if ":" in line)
                    _, target, _ = lines[0].split(" ")
                    if target == "/stats":
                        body = json.dumps({"attempts": attempts,
                                           "connections": connections}).encode()
                        extra = b""
                    else:
                        if not counted:
                            connections += 1
                            counted = True
                        attempts += 1
                        request = await reader.readexactly(int(headers.get("content-length", "0")))
                        size = next((s for s in ("large", "wide") if s in target), "small")
                        service = next(
                            s for s in ("sqs", "dynamodb", "lambda", "s3") if s in target)
                        operation = headers.get("x-amz-target", "")
                        if "sendmessage" in operation:
                            message = json.loads(request)["MessageBody"]
                            body = json.dumps({"MessageId": "sent", "MD5OfMessageBody":
                                               hashlib.md5(message.encode(),
                                               usedforsecurity=False).hexdigest()}).encode()
                        elif "putitem" in operation:
                            item = json.loads(request)["Item"]
                            assert item["pk"]["S"] == "key"
                            assert item["data"]["M"] == {
                                str(i): {"S": "a" * shape(size, service)[1]} for i in range(4)}
                            body = b"{}"
                        elif service == "lambda":
                            assert request == b"a" * MIB
                            body = request
                        elif service == "s3" and "stream" in target:
                            body = b"a" * MIB
                        else:
                            body = bodies[(service, size)]
                        extra = (b"content-type: application/x-amz-json-1.0\r\n"
                                 if service in {"sqs", "dynamodb"} else b"")
                    writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: " + str(len(body)).encode()
                                 + b"\r\n" + extra + b"\r\n" + body)
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=128 * 1024)
        connection.send(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        async with server:
            await server.serve_forever()
    asyncio.run(serve())


def memory():
    import ctypes

    import psutil

    result = {"rss": psutil.Process().memory_info().rss}
    # No trim: distinguish allocator retention from live arena/mmap allocations.
    class Mallinfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_size_t) for name in (
            "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks",
            "fsmblks", "uordblks", "fordblks", "keepcost")]
    try:
        allocator = ctypes.CDLL(None).mallinfo2
        allocator.restype = Mallinfo
        value = allocator()
        result["allocated"] = value.uordblks + value.hblkhd
    except AttributeError:
        pass
    return result


async def peer_stats(endpoint):
    port = int(endpoint.rsplit(":", 1)[1])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /stats HTTP/1.1\r\nHost: localhost\r\n\r\n")
    await writer.drain()
    header = await reader.readuntil(b"\r\n\r\n")
    length = int(header.split(b"content-length: ")[1].split(b"\r\n")[0])
    result = json.loads(await reader.readexactly(length))
    writer.close()
    await writer.wait_closed()
    return result


async def measure(connection, endpoint, case):
    from rboto import ClientConfig, configure_logging, configure_runtime

    service, size, mode = case
    module = importlib.import_module(f"rboto_{service}")
    native = importlib.import_module(f"rboto_{service}._native")
    with Path(native.__file__).open("rb") as library:
        library_sha256 = hashlib.file_digest(library, "sha256").hexdigest()
    client_type = getattr(module, {"sqs": "SQSClient", "s3": "S3Client",
                                  "dynamodb": "DynamoDBClient", "lambda": "LambdaClient"}[service])
    configure_runtime(worker_threads=2)
    client = client_type(
        region="us-east-1", endpoint_url=f"{endpoint}/{service}/{size}",
        config=ClientConfig(access_key_id="testing", secret_access_key="testing",
                            max_attempts=1, operation_timeout=30))
    count, length = shape(size, service)
    message = "a" * length
    entry = []
    entry_method = entry_params = None
    if mode == "entry":
        if service == "sqs":
            entry_method = client._native.send_message
            entry_params = {"queue_url": f"{endpoint}/sqs/{size}/queue", "message_body": message}
        elif service == "dynamodb":
            entry_method = client._native.put_item
            entry_params = {"table_name": "table", "item": {
                "pk": {"s": "key"}, "data": {"m": {
                    str(i): {"s": message} for i in range(4)}}}}
        else:
            entry_method = client._native.invoke
            entry_params = {"function_name": "function", "payload": b"a" * MIB}

    class Destination(io.StringIO):
        def write(self, value):
            if mode == "logging-slow":
                time.sleep(0.005)
            # Discard content, preserving synchronous format/write/flush costs.
            return len(value)

    if mode.startswith("logging"):
        configure_logging(level="OFF" if mode == "logging-off" else "INFO", stream=Destination())

    async def request():
        if mode == "entry":
            start = time.perf_counter_ns()
            pending = entry_method(entry_params)
            entry.append((time.perf_counter_ns() - start) / 1000)
            result = await pending
            if service == "sqs":
                assert result.message_id == "sent"
            elif service == "lambda":
                assert result.payload == b"a" * MIB
            return result
        if service == "sqs":
            return await client.receive_message(queue_url=f"{endpoint}/sqs/{size}/queue")
        if service == "dynamodb":
            return await client.scan(table_name="table")
        if service == "lambda":
            return await client.invoke(function_name="function", payload=b"a" * MIB)
        if mode in {"read", "chunks"}:
            output = await client.get_object(bucket="bucket", key="stream")
            if mode == "read":
                assert await output.body.read() == b"a" * MIB
            else:
                assert b"".join([chunk async for chunk in output.body]) == b"a" * MIB
            return output
        return await client.list_objects_v2(bucket="bucket")

    def access(output):
        if mode == "dict":
            return output.to_dict()
        if service == "sqs":
            return output.messages
        if service == "dynamodb":
            return output.items
        if service == "lambda":
            return output.payload
        return output.contents

    def verify(value):
        if mode == "dict":
            assert value["messages"][0]["body"] == message
        elif service == "sqs":
            assert len(value) == count and value[-1].message_id == str(count - 1)
            assert value[0].body == message
        elif service == "dynamodb":
            assert len(value) == count and value[0]["data"]["m"]["text"]["s"] == message
        elif service == "lambda":
            assert value == b"a" * MIB
        else:
            assert len(value) == count and value[0].owner.id == "owner"

    # Warm the client without warming the response whose first getter is timed.
    await request()
    entry.clear()
    gc.collect()
    empty = memory()
    output = await request()
    held = memory()
    before_stats = await peer_stats(endpoint)
    connection.send({"stage": "ready"})
    if connection.recv() != "start":
        raise RuntimeError("Expected start")
    gaps = []
    done = False

    async def tick():
        previous = time.perf_counter()
        while not done:
            await asyncio.sleep(0.001)
            now = time.perf_counter()
            gaps.append((now - previous) * 1000)
            previous = now

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.01)
    gaps.clear()
    entry.clear()
    latencies = []
    first_us = None
    iterations = 100 if mode in {"getters", "dict"} else 24
    cpu = time.process_time()
    start = time.perf_counter()
    if mode in {"getters", "dict"}:
        first_start = time.perf_counter_ns()
        value = access(output)
        first_us = (time.perf_counter_ns() - first_start) / 1000
        del value
        for _ in range(iterations):
            t = time.perf_counter_ns()
            value = access(output)
            latencies.append((time.perf_counter_ns() - t) / 1000)
            del value
            await asyncio.sleep(0)
    elif mode in {"idle", "blocking"}:
        for _ in range(iterations):
            if mode == "blocking":
                time.sleep(0.01)
            await asyncio.sleep(0.001)
    else:
        async def one():
            t = time.perf_counter_ns()
            result = await request()
            if mode == "consume":
                if service == "sqs":
                    values = result.messages
                    assert len(values) == count
                    for index, item in enumerate(values):
                        assert item.message_id == str(index)
                        assert item.message_attributes["tag"].string_value == "value"
                else:
                    values = result.contents
                    assert len(values) == count
                    for index, item in enumerate(values):
                        assert item.key == str(index) and item.owner.id == "owner"
            latencies.append((time.perf_counter_ns() - t) / 1000)
            return result
        if mode == "parallel":
            # Include concurrent response creation; release all results per batch.
            for _ in range(3):
                results = await asyncio.gather(*(one() for _ in range(8)))
                assert all(len(r.messages) == count for r in results)
                del results
        else:
            for _ in range(iterations):
                await one()
    wall = time.perf_counter() - start
    cpu = time.process_time() - cpu
    await asyncio.sleep(0.002)
    done = True
    await ticker
    after_stats = await peer_stats(endpoint)
    attempts = after_stats["attempts"] - before_stats["attempts"]
    expected_attempts = 0 if mode in {"getters", "dict", "idle", "blocking"} else iterations
    assert attempts == expected_attempts
    retention = {}
    if mode in {"getters", "dict"}:
        values = [access(output) for _ in range(8)]
        verify(values[0])
        retention["eight_views"] = memory()
        child = values[0][0] if isinstance(values[0], list) else None
        del values, output
        gc.collect()
        retention["one_child"] = memory()
        if service == "sqs" and child is not None:
            assert child.body == message
        del child
        gc.collect()
        retention["released"] = memory()
    connection.send({
        "stage": "result", "case": case, "wall_s": wall, "cpu_s": cpu,
        "shape": {"items": count, "payload_bytes": length},
        "first_us": first_us,
        "p50_us": statistics.median(latencies) if latencies else None,
        "p95_us": sorted(latencies)[int(len(latencies) * .95)] if latencies else None,
        "entry_p50_us": statistics.median(entry) if entry else None,
        "loop_max_gap_ms": max(gaps), "loop_p95_gap_ms": sorted(gaps)[int(len(gaps)*.95)],
        "empty": empty, "held": held, "retention": retention,
        "attempts": attempts, "retries": 0,
        "connections": after_stats["connections"] - before_stats["connections"],
        "failures": 0, "iterations": iterations,
        "library_sha256": library_sha256,
    })


def worker(connection, endpoint, case, path):
    sys.path.insert(0, path)
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            os.environ.pop(name)
    os.environ.update(AWS_EC2_METADATA_DISABLED="true", AWS_CONFIG_FILE=os.devnull,
                      AWS_SHARED_CREDENTIALS_FILE=os.devnull)
    try:
        asyncio.run(measure(connection, endpoint, case))
    except BaseException:
        connection.send({"stage": "error", "case": case, "traceback": traceback.format_exc()})
        raise
    finally:
        connection.close()


def main():
    import psutil

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-path", required=True)
    parser.add_argument("--after-path", required=True)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--case", help="Optional service/size/mode")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    peer = ctx.Process(target=peer_worker, args=(child,))
    peer.start()
    endpoint = parent.recv()
    report = {"platform": platform.platform(), "python": sys.version, "rows": [],
              "before_path": args.before_path, "after_path": args.after_path}
    cases = [c for c in CASES if not args.case or "/".join(c) == args.case]
    if not cases:
        raise ValueError("No matching case")
    schedule = [(label, path, case, r) for r in range(args.rounds) for case in cases
                for label, path in (("before", args.before_path), ("after", args.after_path))]
    random.Random(58).shuffle(schedule)
    try:
        for label, path, case, round_id in schedule:
            receiver, sender = ctx.Pipe()
            process = ctx.Process(target=worker, args=(sender, endpoint, case, path))
            process.start()
            peak = 0
            measuring = False
            started = time.monotonic()
            result = None
            try:
                while time.monotonic() - started < 120:
                    if receiver.poll(.001):
                        message = receiver.recv()
                        if message["stage"] == "ready":
                            measuring = True
                            receiver.send("start")
                        else:
                            result = message
                            break
                    try:
                        if measuring:
                            peak = max(peak, psutil.Process(process.pid).memory_info().rss)
                    except psutil.NoSuchProcess:
                        break
                process.join(5)
                if result is None or result["stage"] == "error" or process.exitcode != 0:
                    report.setdefault("failures", []).append(
                        {"label": label, "case": case, "result": result, "exit": process.exitcode})
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
                    raise RuntimeError(f"Failed {label}/{case}: {result}; exit={process.exitcode}")
                result.update(label=label, round=round_id, peak_rss=peak)
                report["rows"].append(result)
                args.output.write_text(json.dumps(report, indent=2) + "\n")
                print(f"{label} {case}: {result['p50_us']} us", flush=True)
            finally:
                if process.is_alive():
                    process.terminate()
                process.join()
                receiver.close()
                sender.close()
    finally:
        peer.terminate()
        peer.join()
        parent.close()
        child.close()


if __name__ == "__main__":
    main()
