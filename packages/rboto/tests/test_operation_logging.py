import asyncio
import logging
from collections.abc import Callable
from contextvars import ContextVar

import pytest
from rboto import (
    Interceptor,
    LogTraceContext,
    Next,
    OperationRequest,
    OperationResponse,
    operation_context,
)
from rboto.exceptions import ClientError
from rboto.interceptors import invoke
from rboto.logging import OperationLogFields, _LoggingInterceptor

SECRET = "credentials-payload-exception-CANARY"


class Output:
    def to_dict(self) -> object:
        pytest.fail("logging serialized the response")

    def __repr__(self) -> str:
        pytest.fail("logging inspected the response")


async def execute(
    *middleware: Interceptor,
    error: BaseException | None = None,
    retries: int = 0,
    service: str = "logging_test",
) -> Output:
    async def native(params: dict[str, object], metadata: dict[str, object]) -> Output:
        await asyncio.sleep(0)
        metadata.update(
            region="us-east-1",
            attempt_count=retries + 1,
            status_code=400 if error else 200,
            request_id="request-123",
            attempts=[
                (number, 0.002, 503 if number <= retries else 200, f"request-{number}")
                for number in range(1, retries + 2)
            ],
        )
        if error:
            raise error
        return Output()

    return await invoke(
        native,
        {"secret": SECRET},
        middleware,
        service,
        "TestOperation",
        None,
        Output,
        ("secret",),
        ("secret",),
    )


def records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name.startswith("rboto.")]


def fields(record: logging.LogRecord) -> OperationLogFields:
    result: OperationLogFields = record.__dict__["rboto"]
    return result


def test_completion_schema_and_retry_details(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="rboto")
    with operation_context(correlation_id="application-request"):
        output = asyncio.run(execute(_LoggingInterceptor(), retries=2))
    assert isinstance(output, Output)
    completion, retry = records(caplog)
    data = fields(completion)
    assert completion.levelno == logging.INFO
    assert completion.msg == "operation.completed"
    assert data == {
        "schema_version": 1,
        "event": "operation.completed",
        "service": "logging_test",
        "operation": "TestOperation",
        "operation_id": data["operation_id"],
        "correlation_id": "application-request",
        "region": "us-east-1",
        "duration_ms": data["duration_ms"],
        "outcome": "success",
        "dispatched": True,
        "native_duration_ms": data["native_duration_ms"],
        "attempt_count": 3,
        "retry_count": 2,
        "status_code": 200,
        "request_id": "request-123",
        "error_code": None,
        "error_type": None,
        "native_error_code": None,
        "native_error_type": None,
    }
    assert data["native_duration_ms"] is not None
    assert data["duration_ms"] >= data["native_duration_ms"] >= 0
    assert len(data["operation_id"]) == 32
    assert retry.levelno == logging.DEBUG
    assert fields(retry)["event"] == "operation.retry"
    assert fields(retry)["operation_id"] == data["operation_id"]
    assert [a["duration_ms"] for a in fields(retry)["attempts"]] == [2, 2, 2]
    assert fields(retry)["attempts_truncated"] is False


@pytest.mark.parametrize(
    ("error", "level", "outcome"),
    [
        (ClientError(SECRET, "AccessDenied", "request-123"), logging.ERROR, "error"),
        (OSError(SECRET), logging.ERROR, "error"),
        (asyncio.CancelledError(SECRET), logging.WARNING, "cancelled"),
    ],
)
def test_failure_identity_and_private_fields(
    caplog: pytest.LogCaptureFixture,
    error: BaseException,
    level: int,
    outcome: str,
) -> None:
    caplog.set_level(logging.DEBUG, logger="rboto")
    with pytest.raises(type(error)) as caught:
        asyncio.run(execute(_LoggingInterceptor(), error=error))
    assert caught.value is error
    [record] = records(caplog)
    data = fields(record)
    assert record.levelno == level and data["outcome"] == outcome
    assert data["error_type"] == type(error).__name__
    assert data["error_code"] == ("AccessDenied" if isinstance(error, ClientError) else None)
    assert record.exc_info is None and record.exc_text is None and record.stack_info is None
    assert SECRET not in repr(record.__dict__)
    assert SECRET not in caplog.text


def test_disabled_logger_skips_clocks_and_trace_lookup(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rboto.logging as module

    caplog.set_level(logging.CRITICAL, logger="rboto")

    def forbidden() -> LogTraceContext:
        pytest.fail("disabled logging prepared metadata")

    monkeypatch.setattr(module, "perf_counter", forbidden)
    asyncio.run(execute(_LoggingInterceptor(trace_context=forbidden)))
    assert records(caplog) == []


def test_suppressed_success_does_not_prepare_fields_or_lookup_trace(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rboto.logging as module

    caplog.set_level(logging.WARNING, logger="rboto")

    def forbidden(**kwargs: object) -> LogTraceContext:
        pytest.fail("suppressed success prepared fields")

    monkeypatch.setattr(module, "OperationLogFields", forbidden)
    asyncio.run(execute(_LoggingInterceptor(trace_context=forbidden)))
    assert records(caplog) == []


def test_registration_is_opt_in_and_does_not_configure_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="rboto")
    logger = logging.getLogger("rboto.logging_test")
    before = (logger.handlers[:], logger.filters[:], logger.level, logger.propagate)
    _LoggingInterceptor()
    asyncio.run(execute())
    assert records(caplog) == []
    asyncio.run(execute(_LoggingInterceptor()))
    assert len(records(caplog)) == 1  # propagated to the root handler exactly once
    assert (logger.handlers, logger.filters, logger.level, logger.propagate) == before


def test_policy_cache_recovery_and_registration_order(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="rboto")

    async def cached(request: OperationRequest, call_next: Next) -> OperationResponse:
        return OperationResponse(Output())

    async def blocked(request: OperationRequest, call_next: Next) -> OperationResponse:
        raise PermissionError(SECRET)

    async def recovered(request: OperationRequest, call_next: Next) -> OperationResponse:
        try:
            return await call_next(request)
        except ClientError:
            return OperationResponse(Output())

    asyncio.run(execute(_LoggingInterceptor(), cached))
    with pytest.raises(PermissionError):
        asyncio.run(execute(_LoggingInterceptor(), blocked))
    asyncio.run(
        execute(
            _LoggingInterceptor(),
            recovered,
            error=ClientError(SECRET, "AccessDenied"),
        )
    )
    asyncio.run(execute(cached, _LoggingInterceptor()))  # cache skips downstream logging
    cache, policy, recovery = [fields(r) for r in records(caplog)]
    assert cache["outcome"] == "success" and not cache["dispatched"]
    assert cache["attempt_count"] == 0 and cache["native_duration_ms"] is None
    assert policy["outcome"] == "error" and not policy["dispatched"]
    assert policy["error_type"] == "PermissionError"
    assert recovery["outcome"] == "success" and recovery["error_code"] is None
    assert recovery["native_error_code"] == "AccessDenied"
    assert SECRET not in caplog.text


def test_trace_and_correlation_are_isolated_across_tasks_and_loops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="rboto")
    trace: ContextVar[LogTraceContext | None] = ContextVar("trace", default=None)
    middleware = _LoggingInterceptor(trace_context=trace.get)

    async def call(number: int) -> None:
        token = trace.set(LogTraceContext(f"trace-{number}", f"span-{number}"))
        try:
            with operation_context(correlation_id=str(number)):
                await execute(middleware, service=f"test_{number % 2}")
        finally:
            trace.reset(token)

    async def run(start: int) -> None:
        await asyncio.gather(*(call(n) for n in range(start, start + 20)))

    asyncio.run(run(0))
    asyncio.run(run(20))
    data = [fields(r) for r in records(caplog)]
    assert len(data) == len({row["operation_id"] for row in data}) == 40
    for row in data:
        assert row["trace_id"] == f"trace-{row['correlation_id']}"
        assert row["span_id"] == f"span-{row['correlation_id']}"
        assert row["service"] == f"test_{int(row['correlation_id'] or '0') % 2}"
    assert trace.get() is None


@pytest.mark.parametrize("fails", [False, True])
def test_broken_handler_does_not_replace_result_or_exception(fails: bool) -> None:
    class BrokenHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise RuntimeError(SECRET)

    logger = logging.getLogger("rboto.broken_handler")
    old_level, old_propagate = logger.level, logger.propagate
    handler = BrokenHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    error = OSError("original")
    try:
        if fails:
            with pytest.raises(OSError) as caught:
                asyncio.run(execute(_LoggingInterceptor(), error=error, service="broken_handler"))
            assert caught.value is error
        else:
            assert isinstance(
                asyncio.run(execute(_LoggingInterceptor(), service="broken_handler")),
                Output,
            )
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(old_level)
        logger.propagate = old_propagate


@pytest.mark.parametrize("provider", [lambda: None, lambda: 1 / 0])
def test_unavailable_trace_still_logs_completion(
    caplog: pytest.LogCaptureFixture,
    provider: Callable[[], LogTraceContext | None],
) -> None:
    caplog.set_level(logging.INFO, logger="rboto")
    asyncio.run(execute(_LoggingInterceptor(trace_context=provider)))
    [record] = records(caplog)
    assert "trace_id" not in fields(record) and "span_id" not in fields(record)
