"""Async operation middleware with typed requests, responses, and native metadata."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import Protocol, TypeVar, cast
from uuid import uuid4

from .exceptions import ClientError


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Identity of one public operation invocation."""

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
class OperationMetadata:
    """Native dispatch metadata, available after next returns or raises."""

    dispatched: bool = False
    region: str | None = None
    duration: float | None = None
    attempt_count: int | None = 0
    status_code: int | None = None
    request_id: str | None = None
    error_code: str | None = None
    error_type: str | None = None
    attempts: tuple[Attempt, ...] = ()
    attempts_truncated: bool = False

    @property
    def retry_count(self) -> int | None:
        return None if self.attempt_count is None else max(0, self.attempt_count - 1)


@dataclass(slots=True)
class _RequestState:
    metadata: OperationMetadata


@dataclass(frozen=True, slots=True)
class OperationRequest:
    """Operation identity and mutable parameters shared with downstream middleware."""

    context: OperationContext
    parameters: dict[str, object] = field(repr=False)
    _state: _RequestState = field(repr=False, compare=False)

    @property
    def metadata(self) -> OperationMetadata:
        return self._state.metadata

    def with_parameters(self, parameters: dict[str, object]) -> OperationRequest:
        """Replace the parameter mapping while retaining this invocation's identity."""
        return replace(self, parameters=parameters)


@dataclass(slots=True)
class OperationResponse:
    """An operation output. The public client method returns this output directly."""

    output: object = field(repr=False)


class Next(Protocol):
    """The remaining middleware and native dispatch for this invocation."""

    def __call__(self, request: OperationRequest, /) -> Awaitable[OperationResponse]: ...


class Interceptor(Protocol):
    """Async middleware that can modify, block, recover, or replace an operation."""

    def __call__(
        self, request: OperationRequest, call_next: Next, /
    ) -> Awaitable[OperationResponse]: ...


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


def _metadata(
    region: str | None, duration: float, raw: dict[str, object], error: BaseException | None
) -> OperationMetadata:
    attempts = cast("list[tuple[int, float, int | None, str | None]]", raw.get("attempts", []))
    request_id = cast("str | None", raw.get("request_id"))
    return OperationMetadata(
        dispatched=True,
        region=cast("str | None", raw.get("region", region)),
        duration=duration,
        attempt_count=cast("int | None", raw.get("attempt_count")),
        status_code=cast("int | None", raw.get("status_code")),
        request_id=request_id or (error.request_id if isinstance(error, ClientError) else None),
        error_code=error.error_code if isinstance(error, ClientError) else None,
        error_type=type(error).__name__ if error is not None else None,
        attempts=tuple(Attempt(*attempt) for attempt in attempts),
        attempts_truncated=bool(raw.get("attempts_truncated", False)),
    )


_Output = TypeVar("_Output")
_UNSET = object()


async def invoke(
    call: Callable[[dict[str, object], dict[str, object]], Awaitable[_Output]],
    params: dict[str, object],
    interceptors: tuple[Interceptor, ...],
    service: str,
    operation: str,
    region: str | None,
    output_type: type[object],
    allowed_parameters: tuple[str, ...],
    required_parameters: tuple[str, ...],
) -> _Output:
    """Internal entry point; generated methods bypass it when middleware is absent."""
    context = OperationContext(uuid4().hex, service, operation, _correlation_id.get())
    state = _RequestState(OperationMetadata(region=region))
    request = OperationRequest(context, params, state)
    native_output: object = _UNSET

    def validate_response(response: object) -> None:
        if not isinstance(response, OperationResponse):
            raise TypeError("interceptors must return OperationResponse")
        # Trust the native adapter's declared return type. Validate replacements
        # and synthetic responses before exposing them to the typed client API.
        if (native_output is _UNSET or response.output is not native_output) and not isinstance(
            response.output, output_type
        ):
            raise TypeError(
                f"{service}.{operation} requires an output of type {output_type.__name__}"
            )

    async def dispatch(index: int, current: OperationRequest) -> OperationResponse:
        nonlocal native_output
        if current.context is not context:
            raise ValueError("next requires a request from the current operation")
        if index == len(interceptors):
            unknown = current.parameters.keys() - allowed_parameters
            missing = [
                key
                for key in required_parameters
                if key not in current.parameters or current.parameters[key] is None
            ]
            if unknown:
                raise TypeError(f"unknown operation parameters: {', '.join(sorted(unknown))}")
            if missing:
                raise TypeError(f"missing required operation parameters: {', '.join(missing)}")
            raw: dict[str, object] = {}
            started = perf_counter()
            error: BaseException | None = None
            try:
                native_output = await call(current.parameters, raw)
            except BaseException as caught:
                error = caught
                raise
            finally:
                state.metadata = _metadata(region, perf_counter() - started, raw, error)
            return OperationResponse(native_output)

        active = True
        used = False
        task = asyncio.current_task()

        async def call_next(forwarded: OperationRequest) -> OperationResponse:
            nonlocal used
            if not active:
                raise RuntimeError("next cannot be called after its interceptor returns")
            if used:
                raise RuntimeError("next can only be called once per interceptor")
            if asyncio.current_task() is not task:
                raise RuntimeError("next must be awaited in the interceptor's task")
            used = True
            return await dispatch(index + 1, forwarded)

        try:
            response = await interceptors[index](current, call_next)
            validate_response(response)
            return response
        finally:
            active = False

    response = await dispatch(0, request)
    return cast(_Output, response.output)
