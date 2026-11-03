"""Worker policy shared by separately compiled service extensions."""

from __future__ import annotations

import os
import sys
from threading import Lock

_lock = Lock()
_configured: int | None = None
_frozen: int | None = None


def _validate(worker_threads: object) -> int:
    if isinstance(worker_threads, bool) or not isinstance(worker_threads, int):
        raise TypeError("worker_threads must be an integer")
    if not 1 <= worker_threads <= sys.maxsize:
        raise ValueError(f"worker_threads must be between 1 and {sys.maxsize}")
    return worker_threads


def configure_runtime(*, worker_threads: int) -> None:
    """Set workers per service before the first native operation starts.

    Overrides TOKIO_WORKER_THREADS. Repeating the frozen value is harmless;
    changing it after runtime initialization raises RuntimeError.
    See [runtime configuration](https://ferrumio.github.io/rboto/guides/runtime/).
    """
    global _configured
    value = _validate(worker_threads)
    with _lock:
        if _frozen is not None and value != _frozen:
            raise RuntimeError(
                f"rboto runtime policy is already frozen at {_frozen} worker threads per service; "
                "call configure_runtime() before the first native operation"
            )
        _configured = value


# Invoked by name from rboto-core, outside the Python reference graph.
def _freeze_worker_threads(available_parallelism: int) -> int:  # pyright: ignore[reportUnusedFunction]
    """Called once per extension at native runtime initialization, never on import."""
    global _frozen
    with _lock:
        if _frozen is not None:
            return _frozen
        if _configured is not None:
            value = _configured
        elif (override := os.environ.get("TOKIO_WORKER_THREADS")) is not None:
            digits = override.removeprefix("+")
            if not digits.isascii() or not digits.isdecimal():
                raise ValueError("TOKIO_WORKER_THREADS must be a positive decimal integer")
            value = _validate(int(override))
        else:
            value = min(2, _validate(available_parallelism))
        _frozen = value
        return value
