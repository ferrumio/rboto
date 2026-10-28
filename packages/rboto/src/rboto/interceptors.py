"""Typed, observational hooks for asynchronous client operations."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, Protocol, TypeVar, cast
from uuid import uuid4

from .exceptions import ClientError


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Identity shared by the start and terminal events of one invocation."""

    operation_id: str
    service: str
    operation: str
    correlation_id: str | None


@dataclass(frozen=True, slots=True)
class Attempt:
    """A completed native attempt; duration is in seconds, excluding backoff."""

    number: int
    duration: float
    status_code: int | None
    request_id: str | None


@dataclass(frozen=True, slots=True)
class OperationEvent:
    """Metadata only: never contains request parameters, outputs, or exceptions."""

    context: OperationContext
    phase: Literal["start", "success", "failure", "cancelled"]
    region: str | None = None
    duration: float | None = None
    attempt_count: int | None = None
    status_code: int | None = None
    request_id: str | None = None
    error_code: str | None = None
    error_type: str | None = None
    attempts: tuple[Attempt, ...] = ()
    attempts_truncated: bool = False

    @property
    def retry_count(self) -> int | None:
        return None if self.attempt_count is None else max(0, self.attempt_count - 1)


class Interceptor(Protocol):
    """A callback invoked sequentially on the operation's Python task."""

    def __call__(self, event: OperationEvent, /) -> Awaitable[None] | None: ...


_correlation_id: ContextVar[str | None] = ContextVar("rboto_correlation_id", default=None)


@contextmanager
def operation_context(*, correlation_id: str | None) -> Generator[None, None, None]:
    """Attach a correlation ID to calls in this context, including child tasks."""
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


def resolve_interceptors(
    interceptors: Sequence[Interceptor] | None,
    inherited: Sequence[Interceptor] = (),
) -> tuple[Interceptor, ...]:
    """Explicit client settings replace inherited settings; () disables them."""
    resolved = tuple(inherited if interceptors is None else interceptors)
    if not all(callable(interceptor) for interceptor in resolved):
        raise TypeError("interceptors must contain callable objects")
    return resolved


async def _notify(
    interceptors: tuple[Interceptor, ...], event: OperationEvent
) -> asyncio.CancelledError | None:
    cancellation: asyncio.CancelledError | None = None
    for interceptor in interceptors:
        try:
            result = interceptor(event)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError as error:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                if event.phase == "start":
                    raise
                # The result is already settled. Finish delivering this terminal
                # event once to the remaining observers, then propagate cancellation.
                cancellation = error
            else:
                _report_callback_error(error)
        except Exception as error:
            _report_callback_error(error)
    return cancellation


def _report_callback_error(error: BaseException) -> None:
    asyncio.get_running_loop().call_exception_handler(
        {"message": "rboto interceptor failed", "exception": error}
    )


def _terminal_event(
    context: OperationContext,
    phase: Literal["success", "failure", "cancelled"],
    region: str | None,
    duration: float,
    metadata: dict[str, object],
    error: BaseException | None = None,
) -> OperationEvent:
    raw_attempts = cast(
        "list[tuple[int, float, int | None, str | None]]", metadata.get("attempts", [])
    )
    request_id = cast("str | None", metadata.get("request_id"))
    return OperationEvent(
        context=context,
        phase=phase,
        region=cast("str | None", metadata.get("region", region)),
        duration=duration,
        attempt_count=cast("int | None", metadata.get("attempt_count")),
        status_code=cast("int | None", metadata.get("status_code")),
        request_id=request_id or (error.request_id if isinstance(error, ClientError) else None),
        error_code=error.error_code if isinstance(error, ClientError) else None,
        error_type=type(error).__name__ if error is not None else None,
        attempts=tuple(Attempt(*attempt) for attempt in raw_attempts),
        attempts_truncated=bool(metadata.get("attempts_truncated", False)),
    )


_Output = TypeVar("_Output")


async def invoke(
    call: Callable[[dict[str, object], dict[str, object]], Awaitable[_Output]],
    params: dict[str, object],
    interceptors: tuple[Interceptor, ...],
    service: str,
    operation: str,
    region: str | None,
) -> _Output:
    """Internal bridge used only when at least one interceptor is configured."""
    context = OperationContext(uuid4().hex, service, operation, _correlation_id.get())
    metadata: dict[str, object] = {}
    started = perf_counter()
    try:
        await _notify(interceptors, OperationEvent(context, "start", region=region))
        output = await call(params, metadata)
    except asyncio.CancelledError as error:
        event = _terminal_event(
            context, "cancelled", region, perf_counter() - started, metadata, error
        )
        await _notify(interceptors, event)
        raise
    except Exception as error:
        event = _terminal_event(
            context, "failure", region, perf_counter() - started, metadata, error
        )
        cancellation = await _notify(interceptors, event)
        if cancellation is not None:
            raise cancellation from error
        raise
    event = _terminal_event(context, "success", region, perf_counter() - started, metadata)
    cancellation = await _notify(interceptors, event)
    if cancellation is not None:
        raise cancellation
    return output
