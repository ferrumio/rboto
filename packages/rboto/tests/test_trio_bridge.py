"""Scheduler contracts and races that do not require a native service wheel."""

from __future__ import annotations

import subprocess
import sys
import threading
from contextvars import ContextVar

import anyio
import pytest
import trio
from rboto import _async_backend
from rboto._trio import NativeContext


class Abort:
    def __init__(self) -> None:
        self.calls = 0

    def cancel(self) -> None:
        self.calls += 1


def test_asyncio_helpers_do_not_import_trio() -> None:
    result = subprocess.run(
        [sys.executable, "-c", """
import asyncio
import sys
from rboto import _async_backend
async def main():
    assert _async_backend.current_task() is asyncio.current_task()
    assert _async_backend.current_time() > 0
    async with _async_backend.timeout_at(None):
        await _async_backend.sleep(0)
asyncio.run(main())
assert 'trio' not in sys.modules
assert 'rboto._trio' not in sys.modules
"""], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_deadline_and_cancellation_identity(backend: str) -> None:
    async def main() -> None:
        assert _async_backend.current_task() is not None
        timer = _async_backend.timeout_at(_async_backend.current_time() + 0.01)
        with pytest.raises(TimeoutError):
            async with timer:
                await _async_backend.sleep(10)
        assert timer.expired()

        timer = _async_backend.timeout_at(None)
        error = TimeoutError("application timeout")
        with pytest.raises(TimeoutError) as caught:
            async with timer:
                raise error
        assert caught.value is error and not timer.expired()

        timer = _async_backend.timeout_at(None)
        with anyio.CancelScope() as scope:
            scope.cancel()
            with pytest.raises(anyio.get_cancelled_exc_class()) as cancelled:
                async with timer:
                    await _async_backend.sleep(0)
            assert _async_backend.is_cancelled(cancelled.value)
        assert not timer.expired()

    anyio.run(main, backend=backend)


def test_trio_future_cancellation_before_and_during_wait() -> None:
    async def main() -> None:
        for ready in (False, True):
            future = NativeContext().future()
            abort = Abort()
            future.bind_cancel(abort)
            if ready:
                future.deliver("late result", None)
            with trio.CancelScope() as scope:
                scope.cancel()
                await future
            assert scope.cancelled_caught
            assert abort.calls == 1
            future.deliver("ignored", None)
            assert future._value is None

        future = NativeContext().future()
        abort = Abort()
        future.bind_cancel(abort)
        with trio.move_on_after(0.01) as scope:
            await future
        assert scope.cancelled_caught and abort.calls == 1

    trio.run(main)


def test_trio_future_result_error_duplicate_and_late_delivery() -> None:
    async def main() -> None:
        context = NativeContext()
        value = object()
        future = context.future()
        thread = threading.Thread(target=future.complete, args=(value, None))
        thread.start()
        assert await future is value
        thread.join()
        with pytest.raises(RuntimeError, match="only be awaited once"):
            await future

        future = context.future()
        future.deliver(value, None)
        future.deliver(object(), None)
        assert await future is value

        error = LookupError("source error")
        future = context.future()
        future.complete(None, error)
        with pytest.raises(LookupError) as caught:
            await future
        assert caught.value is error

    trio.run(main)

    # A completion posted after Trio exits must release its result quietly.
    saved = []
    async def save() -> None:
        saved.append(NativeContext().future())
    trio.run(save)
    saved[0].complete(object(), None)


def test_trio_python_poll_preserves_context_and_stops_on_cancel() -> None:
    async def main() -> None:
        trace: ContextVar[str] = ContextVar("test_trace", default="missing")
        trace.set("caller")
        context = NativeContext()
        trace.set("changed")
        finished = trio.Event()
        entered = trio.Event()
        outcomes = []

        async def source():
            try:
                assert trace.get() == "caller"
                entered.set()
                await trio.sleep_forever()
                yield "unreachable"
            finally:
                finished.set()

        iterator = source()
        poll = context.start_poll(iterator, lambda value, error: outcomes.append((value, error)))
        await entered.wait()
        thread = threading.Thread(target=poll.cancel)
        thread.start()
        with trio.fail_after(1):
            await finished.wait()
        thread.join()
        assert len(outcomes) == 1
        assert isinstance(outcomes[0][1], trio.Cancelled)
        await iterator.aclose()

    trio.run(main)


def test_trio_poll_cancelled_before_start_never_calls_source() -> None:
    async def main() -> None:
        calls = []
        async def source():
            calls.append(True)
            yield "must not be produced"
        iterator = source()
        poll = NativeContext().start_poll(iterator, lambda value, error: None)
        poll._cancel()
        await trio.lowlevel.checkpoint()
        await trio.lowlevel.checkpoint()
        assert not calls
        await iterator.aclose()

    trio.run(main)


def test_trio_poll_scheduled_during_async_generator_shutdown() -> None:
    saved = []
    outcomes = []
    calls = []

    async def source():
        calls.append(True)
        yield b"must not be produced"

    async def main() -> None:
        context = NativeContext()
        async def finalizer():
            try:
                yield
            finally:
                iterator = source()
                saved.append(iterator)
                context.start_poll(
                    iterator, lambda value, error: outcomes.append((value, error)),
                )
        saved.append(finalizer())
        await anext(saved[0])

    # Async generators are finalized after the system nursery has closed.
    # An exception escaping the queued callback would raise TrioInternalError.
    trio.run(main)
    assert not calls
    assert len(outcomes) == 1
    assert outcomes[0][0] is None
    assert isinstance(outcomes[0][1], RuntimeError)
