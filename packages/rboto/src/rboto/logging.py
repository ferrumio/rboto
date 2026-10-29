"""Configure ready-to-use JSON logs for every service client."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from time import perf_counter
from typing import Literal, NotRequired, TextIO, TypedDict, cast

from .exceptions import ClientError
from .interceptors import Interceptor, Next, OperationRequest, OperationResponse

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "OFF"]
_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "OFF": logging.CRITICAL + 1,
}


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
class _LoggingInterceptor:
    """Internal operation logging, automatically outside application middleware."""

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


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "service": record.name.removeprefix("rboto."),
            "message": record.getMessage(),
        }
        metadata: object = record.__dict__.get("rboto")
        if isinstance(metadata, dict):
            document.update(cast("dict[str, object]", metadata))
        return json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class _JsonHandler(logging.Handler):
    def __init__(self, stream: TextIO) -> None:
        super().__init__()
        self.stream = stream
        self.setFormatter(_JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.stream.write(self.format(record) + "\n")
        self.stream.flush()


_configuration_lock = RLock()
_configured = False
_handler: _JsonHandler | None = None
_operation_logging = _LoggingInterceptor()


def get_logger(service: str) -> logging.Logger:
    """Return the named logger for a service, for example get_logger("s3")."""
    from .services import SERVICE_IDS

    if service not in SERVICE_IDS:
        raise ValueError(f"unknown service: {service!r}")
    return logging.getLogger(f"rboto.{service}")


def configure_logging(
    *,
    level: LogLevel = "INFO",
    service_levels: Mapping[str, LogLevel] | None = None,
    stream: TextIO | None = None,
    trace_context: Callable[[], LogTraceContext | None] | None = None,
) -> None:
    """Enable JSON operation logs globally, including already-created clients.

    Reconfiguration replaces the default level and service overrides without
    adding duplicate handlers. OFF disables records. The default output is
    stdout. The application root logger and its handlers are left untouched.
    """
    from .services import SERVICE_IDS

    global _configured, _handler, _operation_logging
    if level not in _LEVELS:
        raise ValueError(f"invalid log level: {level!r}")
    overrides = dict(service_levels or {})
    for service, service_level in overrides.items():
        if service_level not in _LEVELS:
            raise ValueError(f"invalid log level for {service}: {service_level!r}")
        get_logger(service)
    destination = sys.stdout if stream is None else stream
    with _configuration_lock:
        parent = logging.getLogger("rboto")
        if _handler is None:
            _handler = _JsonHandler(destination)
        else:
            _handler.acquire()
            try:
                _handler.stream = destination
            finally:
                _handler.release()
        if _handler not in parent.handlers:
            parent.addHandler(_handler)
        parent.setLevel(_LEVELS[level])
        parent.disabled = False
        parent.propagate = False
        for service in SERVICE_IDS:
            logger = get_logger(service)
            logger.setLevel(_LEVELS[overrides[service]] if service in overrides else logging.NOTSET)
            logger.disabled = False
            logger.propagate = True
        _operation_logging = _LoggingInterceptor(trace_context=trace_context)
        _configured = True


def operation_interceptors(
    service: str,
    interceptors: tuple[Interceptor, ...],
) -> tuple[Interceptor, ...]:
    """Resolve logging at invocation time so global settings affect existing clients."""
    if not _configured or not logging.getLogger(f"rboto.{service}").isEnabledFor(logging.ERROR):
        return interceptors
    return (_operation_logging, *interceptors)
