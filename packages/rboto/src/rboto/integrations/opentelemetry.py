"""Optional tracing and metrics. See https://ferrumio.github.io/rboto/guides/opentelemetry/."""

from __future__ import annotations

import asyncio
from contextvars import Token
from time import perf_counter
from typing import Literal

try:
    from opentelemetry import context, metrics, trace
    from opentelemetry.metrics import Counter, Histogram, Meter, MeterProvider
    from opentelemetry.trace import Span, SpanKind, StatusCode, Tracer, TracerProvider
    from opentelemetry.util.types import AttributeValue
except ImportError as error:
    raise ImportError(
        "OpenTelemetry integration requires opentelemetry-api. "
        "Install it with: pip install 'rboto[opentelemetry]'"
    ) from error

from rboto import _observability
from rboto._telemetry import TelemetryInterceptor, operation_trace
from rboto.interceptors import Next, OperationRequest, OperationResponse
from rboto.logging import LogTraceContext

__all__ = ["OpenTelemetry", "configure_opentelemetry"]

# The AWS SDK conventions are still Development. Pin their attribute vocabulary
# rather than silently changing it with the installed semantic-conventions package.
_SCHEMA_URL = "https://opentelemetry.io/schemas/1.39.0"
_SCOPE = "rboto"
_Outcome = Literal["success", "error", "cancelled"]


def _validate_flag(name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool")


class OpenTelemetry(TelemetryInterceptor):
    """Per-client/Session observer, automatically ordered before other interceptors.

    Explicit instances override global configuration. ``enabled=False`` disables
    telemetry for that client. Providers/exporters remain application-owned.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        tracing: bool = True,
        metrics: bool = True,
        tracer: Tracer | None = None,
        tracer_provider: TracerProvider | None = None,
        meter: Meter | None = None,
        meter_provider: MeterProvider | None = None,
        record_attempts: bool = False,
    ) -> None:
        for name, value in (
            ("enabled", enabled), ("tracing", tracing), ("metrics", metrics),
            ("record_attempts", record_attempts),
        ):
            _validate_flag(name, value)
        if tracer is not None and tracer_provider is not None:
            raise ValueError("pass tracer or tracer_provider, not both")
        if meter is not None and meter_provider is not None:
            raise ValueError("pass meter or meter_provider, not both")
        self._enabled = enabled and (tracing or metrics)
        self._record_attempts = record_attempts
        self._tracer = (
            tracer if tracer is not None else trace.get_tracer(
                _SCOPE, schema_url=_SCHEMA_URL, tracer_provider=tracer_provider,
            )
        ) if enabled and tracing else None
        self._duration: Histogram | None = None
        self._operations: Counter | None = None
        self._errors: Counter | None = None
        self._retries: Counter | None = None
        if enabled and metrics:
            selected_meter = meter if meter is not None else _get_meter(meter_provider)
            self._duration = selected_meter.create_histogram(
                "rboto.client.operation.duration", unit="s",
                description="Logical operation duration, excluding subsequent stream consumption.",
            )
            self._operations = selected_meter.create_counter(
                "rboto.client.operation.count", unit="{operation}",
                description="Completed operations, including short circuits and cancellation.",
            )
            self._errors = selected_meter.create_counter(
                "rboto.client.operation.errors", unit="{error}",
                description="Logical operations that raised, excluding cancellation.",
            )
            self._retries = selected_meter.create_counter(
                "rboto.client.operation.retries", unit="{retry}",
                description="Known native retries within logical operations.",
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def __call__(
        self, request: OperationRequest, call_next: Next, /
    ) -> OperationResponse:
        if not self.enabled:
            return await call_next(request)
        started = perf_counter()
        span: Span | None = None
        context_token: Token[context.Context] | None = None
        log_token: Token[LogTraceContext | None] | None = None
        error: BaseException | None = None
        outcome: _Outcome = "success"
        try:
            if self._tracer is not None:
                try:
                    service = request.context.service_name or request.context.service
                    attributes: dict[str, AttributeValue] = {
                        "rpc.system": "aws-api",
                        "rpc.service": service,
                        "rpc.method": request.context.operation,
                    }
                    if request.metadata.region is not None:
                        attributes["cloud.region"] = request.metadata.region
                    span = self._tracer.start_span(
                        f"{service}.{request.context.operation}",
                        kind=SpanKind.CLIENT, attributes=attributes,
                    )
                    identity = span.get_span_context()
                    # No-op implementations can return an invalid span. Preserve
                    # the caller's active context in that case.
                    if identity.is_valid:
                        context_token = context.attach(trace.set_span_in_context(span))
                        log_token = operation_trace.set(
                            LogTraceContext(f"{identity.trace_id:032x}", f"{identity.span_id:016x}")
                        )
                except Exception:
                    # A provider failure cannot prevent a service call.
                    pass
            return await call_next(request)
        except asyncio.CancelledError as caught:
            error, outcome = caught, "cancelled"
            raise
        except BaseException as caught:
            error, outcome = caught, "error"
            raise
        finally:
            elapsed = perf_counter() - started
            try:
                try:
                    if span is not None and span.is_recording():
                        self._finish_span(span, request, outcome, error)
                except Exception:
                    pass
                try:
                    self._record_metrics(request, outcome, elapsed)
                except Exception:
                    pass
            finally:
                # Keep metric exemplars on the operation context, then always
                # restore the caller's context, even when instrumentation fails.
                try:
                    if log_token is not None:
                        operation_trace.reset(log_token)
                finally:
                    try:
                        if context_token is not None:
                            context.detach(context_token)
                    finally:
                        if span is not None:
                            try:
                                span.end()
                            except Exception:
                                pass

    def _finish_span(
        self, span: Span, request: OperationRequest, outcome: _Outcome,
        error: BaseException | None,
    ) -> None:
        metadata = request.metadata
        span.set_attribute("rboto.operation.outcome", outcome)
        span.set_attribute("rboto.native.dispatched", metadata.dispatched)
        for name, value in (
            ("cloud.region", metadata.region),
            ("aws.request_id", metadata.request_id),
            ("http.response.status_code", metadata.status_code),
            ("rboto.native.attempt_count", metadata.attempt_count),
            ("rboto.native.retry_count", metadata.retry_count),
            ("rboto.native.error.type", metadata.error_type),
        ):
            if value is not None:
                span.set_attribute(name, value)
        if outcome == "error" and error is not None:
            name = type(error).__name__
            span.set_attribute("error.type", name)
            span.set_status(StatusCode.ERROR)
            # Never pass the exception to record_exception: that would include
            # its message and traceback, which can contain request data.
            span.add_event("exception", {"exception.type": name})
        if self._record_attempts:
            span.set_attribute("rboto.native.attempts_truncated", metadata.attempts_truncated)
            for attempt in metadata.attempts:
                attributes: dict[str, AttributeValue] = {
                    "rboto.attempt.number": attempt.number,
                    "rboto.attempt.duration": attempt.duration,
                }
                if attempt.status_code is not None:
                    attributes["http.response.status_code"] = attempt.status_code
                if attempt.request_id is not None:
                    attributes["aws.request_id"] = attempt.request_id
                span.add_event("rboto.attempt", attributes)

    def _record_metrics(
        self, request: OperationRequest, outcome: _Outcome, elapsed: float,
    ) -> None:
        if self._duration is None:
            return
        attributes = {
            "rboto.service": request.context.service,
            "rboto.operation": request.context.operation,
            "rboto.outcome": outcome,
        }
        self._duration.record(elapsed, attributes)
        assert self._operations is not None
        assert self._errors is not None and self._retries is not None
        self._operations.add(1, attributes)
        if outcome == "error":
            self._errors.add(1, attributes)
        retries = request.metadata.retry_count
        if retries is not None:
            self._retries.add(retries, attributes)


def _get_meter(provider: MeterProvider | None) -> Meter:
    return metrics.get_meter(_SCOPE, schema_url=_SCHEMA_URL, meter_provider=provider)


def configure_opentelemetry(
    *,
    enabled: bool = True,
    tracing: bool = True,
    metrics: bool = True,
    tracer: Tracer | None = None,
    tracer_provider: TracerProvider | None = None,
    meter: Meter | None = None,
    meter_provider: MeterProvider | None = None,
    record_attempts: bool = False,
) -> None:
    """Replace global instrumentation, including for already-created clients.

    Repeated calls never stack observers. In-flight operations finish with their
    previous configuration. Explicit client/Session instances take precedence.
    This does not install or shut down providers, processors, readers or exporters.
    """
    observer = OpenTelemetry(
        enabled=enabled, tracing=tracing, metrics=metrics,
        tracer=tracer, tracer_provider=tracer_provider, meter=meter,
        meter_provider=meter_provider, record_attempts=record_attempts,
    )
    _observability.set_telemetry(observer if observer.enabled else None)
