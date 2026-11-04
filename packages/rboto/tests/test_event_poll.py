import asyncio
from collections.abc import AsyncIterator

import pytest
from rboto._streams import _EventPoll


@pytest.mark.asyncio
async def test_cancel_before_scheduling_does_not_advance_source() -> None:
    advanced = False

    async def events() -> AsyncIterator[object]:
        nonlocal advanced
        advanced = True
        yield object()

    source = events()
    poll = _EventPoll(source)
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await poll.receive()
    assert not advanced
    await source.aclose()


@pytest.mark.asyncio
async def test_cancel_stops_only_the_pending_poll() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def events() -> AsyncIterator[object]:
        try:
            started.set()
            await asyncio.Event().wait()
            yield object()
        finally:
            finished.set()

    source = events()
    poll = _EventPoll(source)
    task = asyncio.create_task(poll.receive())
    await started.wait()
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    await source.aclose()


@pytest.mark.asyncio
async def test_completed_poll_does_not_cancel_caller_or_close_source() -> None:
    async def events() -> AsyncIterator[object]:
        yield 1
        yield 2

    source = events()
    poll = _EventPoll(source)
    assert await poll.receive() == 1
    poll.cancel()
    await asyncio.sleep(0)
    assert await anext(source) == 2
    await source.aclose()
