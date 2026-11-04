"""Cancellation boundary for a native consumer's pending Python event poll."""

import asyncio
from collections.abc import AsyncIterator

__all__ = ["_EventPoll"]  # Imported by the native event-stream bridge.


class _EventPoll:
    def __init__(self, iterator: AsyncIterator[object]) -> None:
        self._iterator = iterator
        self._task: asyncio.Task[object] | None = None
        self._cancelled = False

    async def receive(self) -> object:
        if self._cancelled:
            raise asyncio.CancelledError
        self._task = asyncio.current_task()
        try:
            return await anext(self._iterator)
        finally:
            self._task = None

    def cancel(self) -> None:
        # Called on the originating loop, including cancellation that arrived
        # before receive() was first scheduled. Never close the caller's source.
        self._cancelled = True
        if self._task is not None:
            self._task.cancel()
