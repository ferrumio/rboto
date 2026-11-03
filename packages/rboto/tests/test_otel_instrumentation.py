from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Metric
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from opentelemetry.trace import Span, SpanKind, StatusCode, Tracer
from rboto import Session, _observability
from rboto._telemetry import operation_trace
from rboto.exceptions import ServiceError
from rboto.integrations.opentelemetry import OpenTelemetry, configure_opentelemetry
from rboto.interceptors import (
    Interceptor,
    Next,
    OperationRequest,
    OperationResponse,
    invoke,
    resolve_interceptors,
)
from rboto.logging import LogTraceContext, _LoggingInterceptor


@dataclass
class Telemetry:
    provider: TracerProvider
    exporter: InMemorySpanExporter
    meter_provider: MeterProvider
    reader: InMemoryMetricReader

    def observer(self, *, tracing: bool = True, metrics: bool = True) -> OpenTelemetry:
        return OpenTelemetry(
            tracer_provider=self.provider, meter_provider=self.meter_provider,
            record_attempts=True, tracing=tracing, metrics=metrics,
        )

    @property
    def spans(self) -> tuple[ReadableSpan, ...]:
        return self.exporter.get_finished_spans()

    def metrics(self) -> dict[str, Metric]:
        data = self.reader.get_metrics_data()
        assert data is not None
        return {
            metric.name: metric
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }


@pytest.fixture
def telemetry() -> Iterator[Telemetry]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    previous = _observability._telemetry
    yield Telemetry(provider, exporter, meter_provider, reader)
    _observability._telemetry = previous
    provider.shutdown()
    meter_provider.shutdown()


async def native(params: dict[str, object], metadata: dict[str, object]) -> object:
    await asyncio.sleep(0)
    metadata.update(
        region="us-west-2", attempt_count=2, status_code=200, request_id="request-2",
        attempts=[(1, 0.001, 503, "request-1"), (2, 0.002, 200, "request-2")],
    )
    return params["value"]


async def execute(
    interceptors: tuple[Interceptor, ...] = (),
    call: Callable[[dict[str, object], dict[str, object]], Awaitable[object]] = native,
) -> object:
    return await invoke(
        call, {"value": "private-payload"},
        _observability.operation_interceptors("s3", resolve_interceptors(interceptors)),
        "s3", "GetObject", None, str, ("value",), ("value",), service_name="S3",
    )


@pytest.mark.asyncio
async def test_parent_context_concurrency_and_bounded_metadata(telemetry: Telemetry) -> None:
    identities: list[int] = []

    async def probe(request: OperationRequest, call_next: Next) -> OperationResponse:
        before = trace.get_current_span().get_span_context()
        identities.append(before.span_id)
        await asyncio.sleep(0.01)
        assert trace.get_current_span().get_span_context() == before
        return await call_next(request)

    tracer = telemetry.provider.get_tracer("application")
    with tracer.start_as_current_span("parent") as parent:
        context = parent.get_span_context()
        results = await asyncio.gather(*(execute((probe, telemetry.observer())) for _ in range(4)))
        assert results == ["private-payload"] * 4
        assert trace.get_current_span() is parent
        assert operation_trace.get() is None
    spans = [span for span in telemetry.spans if span.name != "parent"]
    assert len(set(identities)) == 4
    assert len(spans) == 4
    for span in spans:
        assert span.parent is not None and span.parent.span_id == context.span_id
        assert span.context is not None and span.context.trace_id == context.trace_id
        assert span.name == "S3.GetObject" and span.kind == SpanKind.CLIENT
        assert span.status.status_code == StatusCode.UNSET
        assert span.attributes is not None
        assert span.attributes["cloud.region"] == "us-west-2"
        assert span.attributes["aws.request_id"] == "request-2"
        assert span.attributes["rboto.native.retry_count"] == 1
        assert span.attributes["rpc.system"] == "aws-api"
        assert len(span.events) == 2
        assert "private-payload" not in span.to_json()
        assert not any(key.startswith("db.") for key in span.attributes)
    instruments = telemetry.metrics()
    assert set(instruments) == {
        "rboto.client.operation.duration", "rboto.client.operation.count",
        "rboto.client.operation.retries",
    }
    for metric in instruments.values():
        for point in metric.data.data_points:
            assert point.attributes == {
                "rboto.service": "s3", "rboto.operation": "GetObject", "rboto.outcome": "success",
            }


@pytest.mark.asyncio
async def test_failures_recovery_short_circuits_and_invalid_outputs(telemetry: Telemetry) -> None:
    failure = ServiceError("secret-response", "SensitiveError", "request-id", "GetObject")

    async def fail(params: dict[str, object], metadata: dict[str, object]) -> object:
        metadata.update(attempt_count=1, status_code=403)
        raise failure

    async def recover(request: OperationRequest, call_next: Next) -> OperationResponse:
        try:
            return await call_next(request)
        except ServiceError:
            return OperationResponse("recovered")

    async def cache(request: OperationRequest, call_next: Next) -> OperationResponse:
        return OperationResponse("cached")

    async def block(request: OperationRequest, call_next: Next) -> OperationResponse:
        raise PermissionError("secret-policy")

    async def invalid(request: OperationRequest, call_next: Next) -> OperationResponse:
        return OperationResponse(42)

    observer = telemetry.observer()
    with pytest.raises(ServiceError) as caught:
        await execute((observer,), fail)
    assert caught.value is failure
    assert await execute((recover, observer), fail) == "recovered"
    assert await execute((cache, observer), fail) == "cached"
    with pytest.raises(PermissionError):
        await execute((block, observer))
    with pytest.raises(TypeError):
        await execute((invalid, observer))
    spans = telemetry.spans
    assert [span.status.status_code for span in spans] == [
        StatusCode.ERROR, StatusCode.UNSET, StatusCode.UNSET, StatusCode.ERROR, StatusCode.ERROR,
    ]
    assert spans[1].attributes is not None
    assert spans[1].attributes["rboto.native.error.type"] == "ServiceError"
    assert spans[2].attributes is not None
    assert spans[2].attributes["rboto.native.dispatched"] is False
    for span in spans:
        assert "secret-" not in span.to_json()
        assert span.status.description is None
        for event in span.events:
            if event.name == "exception":
                assert set(event.attributes or {}) == {"exception.type"}
    metrics = telemetry.metrics()
    points = metrics["rboto.client.operation.errors"].data.data_points
    assert sum(point.value for point in points) == 3


@pytest.mark.asyncio
async def test_cancellation_ends_once_and_keeps_attempts_unknown(telemetry: Telemetry) -> None:
    entered = asyncio.Event()

    async def wait(params: dict[str, object], metadata: dict[str, object]) -> object:
        entered.set()
        await asyncio.Event().wait()
        return "unreachable"

    task = asyncio.create_task(execute((telemetry.observer(),), wait))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(telemetry.spans) == 1
    span = telemetry.spans[0]
    assert span.status.status_code == StatusCode.UNSET
    assert span.attributes is not None
    assert span.attributes["rboto.operation.outcome"] == "cancelled"
    assert "rboto.native.attempt_count" not in span.attributes
    assert not span.events
    assert operation_trace.get() is None
    assert not trace.get_current_span().get_span_context().is_valid
    assert "rboto.client.operation.errors" not in telemetry.metrics()
    assert "rboto.client.operation.retries" not in telemetry.metrics()


@pytest.mark.asyncio
async def test_reconfiguration_snapshot_and_explicit_overrides(telemetry: Telemetry) -> None:
    provider_before = trace.get_tracer_provider()
    meter_before = metrics.get_meter_provider()
    for _ in range(3):
        configure_opentelemetry(
            tracer_provider=telemetry.provider, meter_provider=telemetry.meter_provider,
        )
    assert trace.get_tracer_provider() is provider_before
    assert metrics.get_meter_provider() is meter_before
    await execute()
    assert len(telemetry.spans) == 1
    await execute((OpenTelemetry(enabled=False),))
    assert len(telemetry.spans) == 1
    session = Session(interceptors=(telemetry.observer(),))
    await execute(session.interceptors)
    assert len(telemetry.spans) == 2  # Explicit and global observers never stack.
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(params: dict[str, object], metadata: dict[str, object]) -> object:
        entered.set()
        await release.wait()
        return "done"

    task = asyncio.create_task(execute(call=wait))
    await entered.wait()
    configure_opentelemetry(enabled=False)
    release.set()
    assert await task == "done"
    assert len(telemetry.spans) == 3
    assert _observability.operation_interceptors("s3", ()) == ()
    await execute(session.interceptors)  # Global disable does not disable explicit instances.
    assert len(telemetry.spans) == 4
    with pytest.raises(ValueError, match="at most one"):
        Session(interceptors=(telemetry.observer(), telemetry.observer()))


@pytest.mark.asyncio
async def test_signals_are_independent_and_sampling_is_respected(telemetry: Telemetry) -> None:
    await execute((telemetry.observer(tracing=False),))
    assert not telemetry.spans
    assert telemetry.metrics()
    telemetry.reader.get_metrics_data()
    await execute((telemetry.observer(metrics=False),))
    assert len(telemetry.spans) == 1
    provider = TracerProvider(sampler=ALWAYS_OFF)
    provider.add_span_processor(SimpleSpanProcessor(telemetry.exporter))
    observer = OpenTelemetry(tracer_provider=provider, metrics=False, record_attempts=True)
    await execute((observer,))
    assert len(telemetry.spans) == 1
    provider.shutdown()


@pytest.mark.asyncio
async def test_noop_tracer_preserves_the_application_context(telemetry: Telemetry) -> None:
    tracer = telemetry.provider.get_tracer("application")
    with tracer.start_as_current_span("parent") as parent:
        async def probe(request: OperationRequest, call_next: Next) -> OperationResponse:
            assert trace.get_current_span().get_span_context() == parent.get_span_context()
            result = await call_next(request)
            assert trace.get_current_span().get_span_context() == parent.get_span_context()
            return result

        observer = OpenTelemetry(tracer=trace.NoOpTracer(), metrics=False)
        assert await execute((observer, probe)) == "private-payload"
        assert trace.get_current_span() is parent
    assert len(telemetry.spans) == 1 and telemetry.spans[0].name == "parent"


@pytest.mark.asyncio
async def test_attempt_diagnostics_are_bounded_and_optional(telemetry: Telemetry) -> None:
    async def many(params: dict[str, object], metadata: dict[str, object]) -> object:
        metadata.update(
            attempt_count=100, attempts_truncated=True,
            attempts=[(i, 0.001, 503, f"id-{i}") for i in range(1, 33)],
        )
        return "ok"

    await execute((telemetry.observer(),), many)
    span = telemetry.spans[0]
    assert len(span.events) == 32
    assert span.attributes is not None
    assert span.attributes["rboto.native.retry_count"] == 99
    assert span.attributes["rboto.native.attempts_truncated"] is True
    await execute((OpenTelemetry(tracer_provider=telemetry.provider, metrics=False),), many)
    assert not telemetry.spans[1].events


@pytest.mark.asyncio
async def test_broken_tracer_and_span_do_not_replace_results(telemetry: Telemetry) -> None:
    tracer = Mock(spec=Tracer)
    tracer.start_span.side_effect = RuntimeError("broken provider")
    assert await execute((OpenTelemetry(tracer=tracer, metrics=False),)) == "private-payload"
    tracer.start_span.side_effect = None
    span = Mock(spec=Span)
    span.get_span_context.return_value = trace.INVALID_SPAN_CONTEXT
    span.set_attribute.side_effect = RuntimeError("broken span")
    span.end.side_effect = RuntimeError("broken processor")
    tracer.start_span.return_value = span
    assert await execute((OpenTelemetry(tracer=tracer, metrics=False),)) == "private-payload"
    span.end.assert_called_once()
    assert trace.get_current_span() is not span


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["start", "finish"])
async def test_control_exceptions_also_restore_context(stage: str) -> None:
    class Stop(BaseException):
        pass

    tracer = Mock(spec=Tracer)
    span = Mock(spec=Span)
    span.get_span_context.return_value = trace.INVALID_SPAN_CONTEXT
    if stage == "start":
        span.get_span_context.side_effect = Stop()
    else:
        span.set_attribute.side_effect = Stop()
    tracer.start_span.return_value = span
    before = trace.get_current_span()
    with pytest.raises(Stop):
        await execute((OpenTelemetry(tracer=tracer, metrics=False),))
    assert trace.get_current_span() is before
    assert operation_trace.get() is None
    span.end.assert_called_once()


def test_repeated_event_loops_do_not_leak_context(telemetry: Telemetry) -> None:
    configure_opentelemetry(tracer_provider=telemetry.provider, metrics=False)
    for _ in range(2):
        assert asyncio.run(execute()) == "private-payload"
        assert not trace.get_current_span().get_span_context().is_valid
    assert len(telemetry.spans) == 2
    assert all(span.parent is None for span in telemetry.spans)


def test_optional_dependency_is_not_needed_on_default_path() -> None:
    core = str(Path(__file__).parents[1] / "src")
    program = """
import sys
import rboto
from rboto._observability import operation_interceptors
assert operation_interceptors("s3", ()) == ()
assert not any(name.startswith("opentelemetry") for name in sys.modules)
try:
    import rboto.integrations.opentelemetry
except ImportError as error:
    assert "rboto[opentelemetry]" in str(error)
else:
    raise AssertionError("dependency should be unavailable")
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", program],
        env={**os.environ, "PYTHONPATH": core}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_configuration_rejects_conflicting_providers(telemetry: Telemetry) -> None:
    with pytest.raises(ValueError, match="tracer or"):
        OpenTelemetry(
            tracer=telemetry.provider.get_tracer("custom"), tracer_provider=telemetry.provider,
        )
    with pytest.raises(ValueError, match="meter or"):
        OpenTelemetry(
            meter=telemetry.meter_provider.get_meter("custom"),
            meter_provider=telemetry.meter_provider,
        )


@pytest.mark.asyncio
async def test_log_correlation_and_explicit_trace_provider(
    telemetry: Telemetry, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rboto.logging as module

    monkeypatch.setattr(module, "_configured", True)
    monkeypatch.setattr(module, "_operation_logging", _LoggingInterceptor())
    with caplog.at_level(logging.INFO, logger="rboto.s3"):
        await execute((telemetry.observer(),))
    span = telemetry.spans[0]
    assert span.context is not None
    fields = caplog.records[0].__dict__["rboto"]
    assert fields["trace_id"] == f"{span.context.trace_id:032x}"
    assert fields["span_id"] == f"{span.context.span_id:016x}"
    assert operation_trace.get() is None
    caplog.clear()
    monkeypatch.setattr(
        module, "_operation_logging",
        _LoggingInterceptor(trace_context=lambda: LogTraceContext("custom-trace", "custom-span")),
    )
    with caplog.at_level(logging.INFO, logger="rboto.s3"):
        await execute((telemetry.observer(),))
    assert caplog.records[0].__dict__["rboto"]["trace_id"] == "custom-trace"


@pytest.mark.asyncio
async def test_metric_failures_and_invalid_reconfiguration_preserve_calls(
    telemetry: Telemetry,
) -> None:
    meter = Mock()
    meter.create_histogram.return_value.record.side_effect = RuntimeError("broken reader")
    observer = OpenTelemetry(tracing=False, meter=meter)
    assert await execute((observer,)) == "private-payload"
    failure = ValueError("original")

    async def fail(params: dict[str, object], metadata: dict[str, object]) -> object:
        raise failure

    with pytest.raises(ValueError) as caught:
        await execute((observer,), fail)
    assert caught.value is failure
    configure_opentelemetry(tracer_provider=telemetry.provider, metrics=False)
    selected = _observability._telemetry
    with pytest.raises(ValueError):
        configure_opentelemetry(
            tracer=telemetry.provider.get_tracer("custom"), tracer_provider=telemetry.provider,
        )
    assert _observability._telemetry is selected
