"""User-facing logging configuration in isolated application processes."""

import subprocess
import sys

PREFIX = """
import asyncio
import io
import json
import logging
from rboto import configure_logging, get_logger, operation_context
from rboto.interceptors import invoke
from rboto.logging import operation_interceptors

class Output:
    def __repr__(self):
        raise AssertionError("response inspected")
    def to_dict(self):
        raise AssertionError("response serialized")

async def call(service="s3", error=None, middleware=()):
    async def native(params, metadata):
        await asyncio.sleep(0)
        metadata.update(attempt_count=1, status_code=200, request_id="request-id")
        if error is not None:
            raise error
        return Output()
    chain = operation_interceptors(service, middleware)
    if not chain:
        return await native({}, {})
    return await invoke(native, {}, chain, service, "TestOperation",
                        "us-east-1", Output, (), ())
"""


def run_application(source: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", PREFIX + source],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    return result.stdout


def test_json_defaults_and_no_root_reconfiguration() -> None:
    output = run_application("""
root = logging.getLogger()
handler = logging.StreamHandler()
root.addHandler(handler)
snapshot = root.handlers[:], root.level, root.disabled
assert not operation_interceptors("s3", ())
configure_logging()
asyncio.run(call())
assert (root.handlers, root.level, root.disabled) == snapshot
assert get_logger("s3").name == "rboto.s3"
assert get_logger("s3") is logging.getLogger("rboto.s3")
""")
    import json

    [line] = output.splitlines()
    record = json.loads(line)
    assert record["logger"] == "rboto.s3" and record["service"] == "s3"
    assert record["level"] == "INFO" and record["outcome"] == "success"
    assert record["timestamp"].endswith("+00:00")
    assert record["schema_version"] == 1 and record["request_id"] == "request-id"


def test_reconfigure_disable_and_service_levels_without_duplicate_handlers() -> None:
    run_application("""
stream = io.StringIO()
configure_logging(service_levels={"s3": "OFF"}, stream=stream)
assert not operation_interceptors("s3", ())
asyncio.run(call("s3"))
asyncio.run(call("dynamodb"))
assert len(stream.getvalue().splitlines()) == 1
configure_logging(service_levels={"dynamodb": "OFF"}, stream=stream)
asyncio.run(call("s3"))
asyncio.run(call("dynamodb"))
assert len(stream.getvalue().splitlines()) == 2
assert len(logging.getLogger("rboto").handlers) == 1
get_logger("s3").disabled = True
assert not operation_interceptors("s3", ())
asyncio.run(call("s3"))
get_logger("s3").disabled = False
get_logger("s3").setLevel(logging.DEBUG)
asyncio.run(call("s3"))
assert len(stream.getvalue().splitlines()) == 3
configure_logging(level="OFF", stream=stream)
assert not operation_interceptors("s3", ())
assert not operation_interceptors("dynamodb", ())
configure_logging(level="OFF", service_levels={"s3": "INFO"}, stream=stream)
asyncio.run(call("s3"))
assert not operation_interceptors("dynamodb", ())
assert len(stream.getvalue().splitlines()) == 4
""")


def test_invalid_configuration_keeps_the_previous_settings() -> None:
    run_application("""
stream = io.StringIO()
configure_logging(stream=stream)
for settings in [
    {"level": "INVALID"}, {"service_levels": {"s3": "INVALID"}},
    {"service_levels": {"s33": "OFF"}},
]:
    try:
        configure_logging(**settings)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid configuration accepted")
asyncio.run(call())
assert len(stream.getvalue().splitlines()) == 1
""")


def test_global_logging_wraps_application_middleware_and_preserves_failures() -> None:
    run_application("""
from rboto import OperationResponse
stream = io.StringIO()
configure_logging(stream=stream)
async def cache(request, call_next):
    return OperationResponse(Output())
async def block(request, call_next):
    raise PermissionError("private-CANARY")
asyncio.run(call(middleware=(cache,)))
try:
    asyncio.run(call(middleware=(block,)))
except PermissionError:
    pass
error = OSError("private-CANARY")
try:
    asyncio.run(call(error=error))
except OSError as caught:
    assert caught is error
rows = [json.loads(line) for line in stream.getvalue().splitlines()]
assert len(rows) == 3
assert rows[0]["outcome"] == "success" and not rows[0]["dispatched"]
assert rows[1]["outcome"] == "error" and not rows[1]["dispatched"]
assert rows[2]["outcome"] == "error" and rows[2]["dispatched"]
assert "CANARY" not in stream.getvalue()
""")


def test_context_trace_and_json_escaping_are_isolated() -> None:
    run_application("""
from contextvars import ContextVar
from rboto import LogTraceContext
trace = ContextVar("trace", default=None)
stream = io.StringIO()
configure_logging(stream=stream, trace_context=trace.get)
async def task(index):
    token = trace.set(LogTraceContext(f"trace-{index}", f"span-{index}"))
    try:
        with operation_context(correlation_id=f"request-{index}\\nsecond line"):
            await call("s3" if index % 2 else "dynamodb")
    finally:
        trace.reset(token)
async def main():
    await asyncio.gather(*(task(i) for i in range(30)))
asyncio.run(main())
rows = [json.loads(line) for line in stream.getvalue().splitlines()]
assert len(rows) == len({r["operation_id"] for r in rows}) == 30
for row in rows:
    index = int(row["trace_id"].removeprefix("trace-"))
    assert row["correlation_id"] == f"request-{index}\\nsecond line"
    assert row["span_id"] == f"span-{index}"
""")


def test_broken_json_destination_preserves_result_and_original_error() -> None:
    run_application("""
class BrokenStream(io.StringIO):
    def write(self, value):
        raise OSError("log destination failed")
configure_logging(stream=BrokenStream())
assert isinstance(asyncio.run(call()), Output)
error = RuntimeError("original")
try:
    asyncio.run(call(error=error))
except RuntimeError as caught:
    assert caught is error
else:
    raise AssertionError("original error lost")
""")
