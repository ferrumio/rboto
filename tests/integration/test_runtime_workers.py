"""Fresh-process runtime lifecycle checks through compiled S3 and SQS clients."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
SERVICES = [service for service in ("s3", "sqs") if not SELECTED or service in SELECTED]
PAYLOAD = b"runtime-test"


@pytest.fixture
def endpoint() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.reply(PAYLOAD, "application/octet-stream")

        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["MessageBody"] == PAYLOAD.decode()
            self.reply(json.dumps({
                "MessageId": "test-message",
                "MD5OfMessageBody": hashlib.md5(PAYLOAD, usedforsecurity=False).hexdigest(),
            }).encode(), "application/x-amz-json-1.0")

        def reply(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


PROBE = r'''
import asyncio
import gc
import importlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

def thread_count():
    return len(os.listdir("/proc/self/task")) if os.path.isdir("/proc/self/task") else None

initial_threads = thread_count()
from rboto import ClientConfig, configure_runtime

settings = json.loads(sys.argv[1])
services = settings["services"]
endpoint = settings["endpoint"]
modules = {name: importlib.import_module(f"rboto_{name}._native") for name in services}
assert all(module._runtime_worker_threads() is None for module in modules.values())
assert thread_count() == initial_threads
config = ClientConfig(access_key_id="testing", secret_access_key="testing",
                      max_attempts=1, operation_timeout=10)
classes = {
    name: getattr(importlib.import_module("rboto" if settings["facade"] else f"rboto_{name}"),
                  "S3Client" if name == "s3" else "SQSClient")
    for name in services
}
clients = {name: cls(region="us-east-1", endpoint_url=endpoint, config=config)
           for name, cls in classes.items()}
assert all(module._runtime_worker_threads() is None for module in modules.values())
assert thread_count() == initial_threads

# Direct native calls without a running loop fail before runtime initialization.
for name, module in modules.items():
    native = getattr(module, "S3Client" if name == "s3" else "SQSClient")(
        region="us-east-1", endpoint_url=endpoint, config=config)
    try:
        if name == "s3":
            native.get_object({"bucket": "bucket", "key": "key"})
        else:
            native.send_message({"queue_url": endpoint + "/queue", "message_body": "runtime-test"})
    except RuntimeError as error:
        assert "no running event loop" in str(error), error
    else:
        raise AssertionError("Native call outside an event loop succeeded")
    assert module._runtime_worker_threads() is None
if settings["workers"] is not None:
    configure_runtime(worker_threads=settings["workers"])

async def call(name, client=None):
    client = client or clients[name]
    if name == "s3":
        response = await client.get_object(bucket="bucket", key="key")
        assert await response.body.read() == b"runtime-test"
        assert await response.body.read() == b""
    else:
        response = await client.send_message(queue_url=endpoint + "/queue",
                                             message_body="runtime-test")
        assert response.message_id == "test-message"

if settings["concurrent"]:
    barrier = Barrier(8)
    def start(index):
        barrier.wait(timeout=10)
        asyncio.run(call(services[index % len(services)]))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(start, range(8)))
else:
    asyncio.run(call(services[0]))
    first = modules[services[0]]._runtime_worker_threads()
    # A later service must inherit the same policy even if the environment changes.
    os.environ["TOKIO_WORKER_THREADS"] = str(first + 3)
    for name in services[1:]:
        assert modules[name]._runtime_worker_threads() is None
        asyncio.run(call(name))

counts = {name: module._runtime_worker_threads() for name, module in modules.items()}
assert len(set(counts.values())) == 1, counts
workers = next(iter(counts.values()))
configure_runtime(worker_threads=workers)
try:
    configure_runtime(worker_threads=workers + 1)
except RuntimeError as error:
    assert "already frozen" in str(error)
else:
    raise AssertionError("Late policy change succeeded")

# The same service runtime survives new clients and repeated asyncio.run loops.
for name, cls in classes.items():
    second = cls(region="us-east-1", endpoint_url=endpoint, config=config)
    asyncio.run(call(name, second))
    asyncio.run(call(name))
    assert modules[name]._runtime_worker_threads() == workers
del clients, second
gc.collect()
assert {name: module._runtime_worker_threads() for name, module in modules.items()} == counts
print(json.dumps(counts))
'''


def probe(
    endpoint: str, services: list[str], *, workers: int | None = None,
    override: str | None = None, facade: bool = True, concurrent: bool = False,
) -> dict[str, int]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("AWS_") and key != "TOKIO_WORKER_THREADS"
        and key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
    }
    environment.update({
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_CONFIG_FILE": os.devnull,
        "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
    })
    if override is not None:
        environment["TOKIO_WORKER_THREADS"] = override
    result = subprocess.run(
        [sys.executable, "-c", PROBE, json.dumps({
            "services": services, "endpoint": endpoint, "workers": workers,
            "facade": facade, "concurrent": concurrent,
        })],
        env=environment, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
    counts: dict[str, int] = json.loads(result.stdout)
    return counts


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("facade", [True, False], ids=["facade", "direct"])
@pytest.mark.parametrize(
    ("workers", "override", "expected"),
    [(None, None, None), (None, "4", 4), (1, "invalid", 1), (2, "4", 2)],
)
def test_lazy_runtime_and_policy_precedence(
    endpoint: str, service: str, facade: bool,
    workers: int | None, override: str | None, expected: int | None,
) -> None:
    actual = probe(endpoint, [service], workers=workers, override=override, facade=facade)[service]
    assert actual in {1, 2} if expected is None else actual == expected


@pytest.mark.parametrize("service", SERVICES)
def test_concurrent_first_use_from_multiple_python_loops(endpoint: str, service: str) -> None:
    assert probe(endpoint, [service], workers=2, concurrent=True) == {service: 2}


@pytest.mark.skipif(set(SERVICES) != {"s3", "sqs"}, reason="requires both S3 and SQS")
@pytest.mark.parametrize("concurrent", [False, True])
def test_separate_wheels_share_policy_and_keep_separate_runtimes(
    endpoint: str, concurrent: bool,
) -> None:
    assert probe(endpoint, SERVICES, workers=2, concurrent=concurrent) == {"s3": 2, "sqs": 2}
