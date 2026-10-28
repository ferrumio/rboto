"""Opt-in operation metadata through Python's standard logging module."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, NotRequired, TypedDict

from .exceptions import ClientError
from .interceptors import Next, OperationRequest, OperationResponse


@dataclass(frozen=True, slots=True)
class LogTraceContext:
    """Trace identifiers supplied by the application's current tracing context."""

    trace_id: str
    span_id: str


class AttemptLogFields(TypedDict):
    number: int
    duration_ms: float
    status_code: int | None
    request_id: str | None


class OperationLogFields(TypedDict):
    schema_version: Literal[1]
    event: Literal["operation.completed", "operation.retry"]
    service: str
    operation: str
    operation_id: str
    correlation_id: str | None
    region: str | None
    duration_ms: float
    outcome: Literal["success", "error", "cancelled"]
    dispatched: bool
    native_duration_ms: float | None
    attempt_count: int | None
    retry_count: int | None
    status_code: int | None
    request_id: str | None
    error_code: str | None
    error_type: str | None
    native_error_code: str | None
    native_error_type: str | None
    trace_id: NotRequired[str]
    span_id: NotRequired[str]
    attempts: NotRequired[list[AttemptLogFields]]
    attempts_truncated: NotRequired[bool]


@dataclass(frozen=True, slots=True)
class LoggingInterceptor:
    """Log one completion and optional retry diagnostics without changing the result.

    Register first to include downstream cache hits, policy failures, and
    postprocessing. Handlers, formatters, levels, and propagation belong to the
    application. A trace provider is optional and has no telemetry dependency.
    """

    trace_context: Callable[[], LogTraceContext | None] | None = None

    async def __call__(self, request: OperationRequest, call_next: Next) -> OperationResponse:
        logger = logging.getLogger(f"rboto.{request.context.service}")
        # ERROR is the highest level emitted here. If it is disabled, every
        # lower level is disabled too; skip clocks, fields, and trace lookup.
        if not logger.isEnabledFor(logging.ERROR):
            return await call_next(request)

        started = perf_counter()
        error: BaseException | None = None
        outcome: Literal["success", "error", "cancelled"] = "success"
        try:
            return await call_next(request)
        except asyncio.CancelledError as caught:
            error, outcome = caught, "cancelled"
            raise
        except BaseException as caught:
            error, outcome = caught, "error"
            raise
        finally:
            try:
                self._emit(logger, request, started, outcome, error)
            except Exception:
                # A broken application handler/filter must not replace an
                # operation result or mask its original exception.
                pass

    def _emit(
        self,
        logger: logging.Logger,
        request: OperationRequest,
        started: float,
        outcome: Literal["success", "error", "cancelled"],
        error: BaseException | None,
    ) -> None:
        level = (
            logging.INFO
            if outcome == "success"
            else logging.WARNING
            if outcome == "cancelled"
            else logging.ERROR
        )
        completed = logger.isEnabledFor(level)
        metadata = request.metadata
        retries = (
            logger.isEnabledFor(logging.DEBUG)
            and metadata.retry_count is not None
            and metadata.retry_count > 0
        )
        if not completed and not retries:
            return

        fields = OperationLogFields(
            schema_version=1,
            event="operation.completed",
            service=request.context.service,
            operation=request.context.operation,
            operation_id=request.context.operation_id,
            correlation_id=request.context.correlation_id,
            region=metadata.region,
            duration_ms=(perf_counter() - started) * 1000,
            outcome=outcome,
            dispatched=metadata.dispatched,
            native_duration_ms=(
                metadata.duration * 1000 if metadata.duration is not None else None
            ),
            attempt_count=metadata.attempt_count,
            retry_count=metadata.retry_count,
            status_code=metadata.status_code,
            request_id=metadata.request_id,
            error_code=error.error_code if isinstance(error, ClientError) else None,
            error_type=type(error).__name__ if error is not None else None,
            native_error_code=metadata.error_code,
            native_error_type=metadata.error_type,
        )
        if self.trace_context is not None:
            try:
                trace = self.trace_context()
                if trace is not None:
                    fields["trace_id"] = trace.trace_id
                    fields["span_id"] = trace.span_id
            except Exception:
                # Trace lookup is best effort; still emit the operation record.
                pass
        if completed:
            logger.log(level, "operation.completed", extra={"rboto": fields})
        if retries:
            retry_fields = fields.copy()
            retry_fields["event"] = "operation.retry"
            retry_fields["attempts"] = [
                AttemptLogFields(
                    number=attempt.number,
                    duration_ms=attempt.duration * 1000,
                    status_code=attempt.status_code,
                    request_id=attempt.request_id,
                )
                for attempt in metadata.attempts
            ]
            retry_fields["attempts_truncated"] = metadata.attempts_truncated
            logger.debug("operation.retry", extra={"rboto": retry_fields})
