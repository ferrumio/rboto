"""Shared scheduler operations without importing an optional backend eagerly."""

from __future__ import annotations

import asyncio
import sys
from types import TracebackType
from typing import Protocol


class Deadline(Protocol):
    async def __aenter__(self) -> object: ...
    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        tb: TracebackType | None, /,
    ) -> bool | None: ...
    def expired(self) -> bool: ...


def in_trio() -> bool:
    module = sys.modules.get("trio")
    if module is None or not hasattr(module, "lowlevel"):
        return False
    from trio.lowlevel import in_trio_task
    return in_trio_task()


def current_task() -> object:
    if in_trio():
        from trio.lowlevel import current_task as trio_task
        return trio_task()
    return asyncio.current_task()


def current_time() -> float:
    if in_trio():
        from trio import current_time as trio_time
        return trio_time()
    return asyncio.get_running_loop().time()


async def sleep(delay: float) -> None:
    if in_trio():
        from trio import sleep as trio_sleep
        await trio_sleep(delay)
    else:
        await asyncio.sleep(delay)


def timeout_at(when: float | None) -> Deadline:
    if in_trio():
        from ._trio import Deadline as TrioDeadline
        return TrioDeadline(when)
    return asyncio.timeout_at(when)


def is_cancelled(error: BaseException) -> bool:
    if isinstance(error, asyncio.CancelledError):
        return True
    module = sys.modules.get("trio")
    if module is not None and hasattr(module, "Cancelled"):
        from trio import Cancelled
        return isinstance(error, Cancelled)
    return False
