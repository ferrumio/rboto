"""Resolve optional observers without importing any telemetry dependency."""

from ._telemetry import TelemetryInterceptor
from .interceptors import Interceptor
from .logging import operation_interceptors as logging_interceptors

_telemetry: TelemetryInterceptor | None = None


def set_telemetry(observer: TelemetryInterceptor | None) -> None:
    global _telemetry
    _telemetry = observer


def operation_interceptors(
    service: str, interceptors: tuple[Interceptor, ...],
) -> tuple[Interceptor, ...]:
    selected = _telemetry
    if interceptors and isinstance(interceptors[0], TelemetryInterceptor):
        selected, interceptors = interceptors[0], interceptors[1:]
    chain = logging_interceptors(service, interceptors)
    if selected is None or not selected.enabled:
        return chain
    return (selected, *chain)
