"""Dependency-free contracts shared by the optional observer and logging."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .interceptors import Next, OperationRequest, OperationResponse
    from .logging import LogTraceContext

operation_trace: ContextVar[LogTraceContext | None] = ContextVar(
    "rboto_operation_trace", default=None,
)


class TelemetryInterceptor(ABC):
    """Internal marker for the observer outside logging and application policy."""

    @property
    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    async def __call__(
        self, request: OperationRequest, call_next: Next, /
    ) -> OperationResponse: ...
