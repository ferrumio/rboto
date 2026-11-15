"""Private Trio bridge; imported only while Trio is running."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextvars import Context, copy_context
from types import TracebackType
from typing import Protocol

import trio


class _Abort(Protocol):
    def cancel(self) -> None: ...


class _Iterator(Protocol):
    async def __anext__(self) -> object: ...


class _Future:
    """One native operation, delivered and cancelled on its originating Trio run."""

    def __init__(self, token: trio.lowlevel.TrioToken) -> None:
        self._token = token
        self._event = trio.Event()
        self._abort: _Abort | None = None
        self._value: object = None
        self._error: BaseException | None = None
        self._done = False
        self._cancelled = False
        self._awaited = False

    def bind_cancel(self, abort: _Abort) -> None:
        self._abort = abort

    def complete(self, value: object, error: BaseException | None) -> None:
        # Called from a Tokio blocking worker. Only the Trio thread mutates state.
        try:
            self._token.run_sync_soon(self.deliver, value, error)
        except trio.RunFinishedError:
            # Dropping the result here also drops any returned native stream owner.
            pass

    def deliver(self, value: object, error: BaseException | None) -> None:
        if self._cancelled or self._done:
            return
        self._done = True
        self._value, self._error = value, error
        self._event.set()

    async def _wait(self) -> object:
        if self._awaited:
            raise RuntimeError("native awaitable can only be awaited once")
        self._awaited = True
        try:
            await self._event.wait()
        except BaseException:
            self._cancelled = True
            if self._abort is not None:
                self._abort.cancel()
            self._value, self._error = None, None
            raise
        finally:
            self._abort = None
        value, error = self._value, self._error
        self._value, self._error = None, None
        if error is not None:
            raise error
        return value

    def __await__(self) -> Generator[object, None, object]:
        return self._wait().__await__()


class _Poll:
    """One owned Python iterator advance; never a detached producer loop."""

    def __init__(
        self, token: trio.lowlevel.TrioToken, context: Context,
        iterator: _Iterator, complete: Callable[[object, BaseException | None], None],
    ) -> None:
        self._token = token
        self._context = context
        self._iterator = iterator
        self._complete: Callable[[object, BaseException | None], None] | None = complete
        self._scope: trio.CancelScope | None = None
        self._cancelled = False

    def start(self) -> None:
        self._token.run_sync_soon(self._spawn)

    def _spawn(self) -> None:
        if self._cancelled:
            self._complete = None
            return
        try:
            trio.lowlevel.spawn_system_task(
                self._run, name="rboto input poll", context=self._context.copy(),
            )
        except RuntimeError as error:
            # Trio may still run queued callbacks after closing its system
            # nursery. Report shutdown to Rust instead of crashing the run.
            self._finish(None, error)

    async def _run(self) -> None:
        # System-task errors must be returned to Rust, never escape into Trio's
        # system nursery. Native ownership cancels this individual poll on drop.
        try:
            if self._cancelled:
                return
            with trio.CancelScope() as scope:
                self._scope = scope
                try:
                    value = await anext(self._iterator)
                except BaseException as error:
                    self._finish(None, error)
                else:
                    self._finish(value, None)
        finally:
            self._scope = None
            self._complete = None

    def _finish(self, value: object, error: BaseException | None) -> None:
        complete, self._complete = self._complete, None
        if complete is not None:
            complete(value, error)

    def cancel(self) -> None:
        try:
            self._token.run_sync_soon(self._cancel)
        except trio.RunFinishedError:
            pass

    def _cancel(self) -> None:
        self._cancelled = True
        if self._scope is not None:
            self._scope.cancel()
        else:
            self._complete = None


class NativeContext:
    def __init__(self) -> None:
        self._token = trio.lowlevel.current_trio_token()
        self._context = copy_context()

    def future(self) -> _Future:
        return _Future(self._token)

    def ready(self, value: object) -> _Future:
        future = self.future()
        future.deliver(value, None)
        return future

    def start_poll(
        self, iterator: _Iterator, complete: Callable[[object, BaseException | None], None],
    ) -> _Poll:
        poll = _Poll(self._token, self._context, iterator, complete)
        poll.start()
        return poll


class Deadline:
    def __init__(self, when: float | None) -> None:
        self._scope = trio.CancelScope(deadline=float("inf") if when is None else when)

    async def __aenter__(self) -> None:
        self._scope.__enter__()

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        suppressed = self._scope.__exit__(exc_type, exc, tb)
        if self._scope.cancelled_caught:
            raise TimeoutError
        return suppressed

    def expired(self) -> bool:
        return self._scope.cancelled_caught
