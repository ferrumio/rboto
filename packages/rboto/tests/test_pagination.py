import asyncio
from dataclasses import dataclass, field
from typing import cast

import pytest
from rboto._pagination import PaginationSpec, paginate

SINGLE = PaginationSpec(("token",), ((("next_token",),),))


@dataclass
class Call:
    pages: list[object]
    requests: list[dict[str, object]] = field(default_factory=list)

    async def __call__(self, **params: object) -> object:
        self.requests.append(params)
        return self.pages[len(self.requests) - 1]


@pytest.mark.asyncio
async def test_lazy_iteration_preserves_pages_and_does_not_prefetch() -> None:
    first = {"items": [], "next_token": "a"}
    last = {"items": [1], "next_token": None}
    call = Call([first, last])
    pages = paginate(call, {"limit": 4}, SINGLE)
    assert call.requests == []
    assert await anext(pages) is first
    assert call.requests == [{"limit": 4}]
    assert await anext(pages) is last
    assert call.requests == [{"limit": 4}, {"limit": 4, "token": "a"}]
    with pytest.raises(StopAsyncIteration):
        await anext(pages)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", {}, [], b""])
async def test_absent_token_stops(token: object) -> None:
    call = Call([{"next_token": token}])
    assert len([page async for page in paginate(call, {}, SINGLE)]) == 1


@pytest.mark.asyncio
async def test_zero_token_and_supplied_starting_cursor() -> None:
    call = Call([{"next_token": 0}, {"next_token": None}])
    assert len([page async for page in paginate(call, {"token": 9}, SINGLE)]) == 2
    assert call.requests == [{"token": 9}, {"token": 0}]


@pytest.mark.asyncio
async def test_repeated_cursor_stops_including_initial_cursor() -> None:
    call = Call([{"next_token": "same"}, {"next_token": "same"}])
    assert len([page async for page in paginate(call, {}, SINGLE)]) == 2
    resumed = Call([{"next_token": "same"}])
    assert len([page async for page in paginate(resumed, {"token": "same"}, SINGLE)]) == 1


@pytest.mark.asyncio
async def test_composite_cursor_clears_missing_component() -> None:
    spec = PaginationSpec(
        ("key", "version"), ((("next_key",),), (("next_version",),)), ("truncated",),
    )
    call = Call([
        {"next_key": "a", "next_version": "v1", "truncated": True},
        {"next_key": "a", "next_version": None, "truncated": True},
        {"next_key": "b", "next_version": "v2", "truncated": False},
    ])
    assert len([page async for page in paginate(call, {}, spec)]) == 3
    assert call.requests == [{}, {"key": "a", "version": "v1"}, {"key": "a"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [False, None, True])
async def test_completion_flag_and_missing_token_both_stop(flag: bool | None) -> None:
    spec = PaginationSpec(("token",), ((("next_token",),),), ("more",))
    call = Call([{"next_token": None if flag else "unused", "more": flag}])
    assert len([page async for page in paginate(call, {}, spec)]) == 1


@pytest.mark.asyncio
async def test_nested_native_paths_and_last_item_fallback() -> None:
    @dataclass
    class Page:
        marker: str | None
        items: list[dict[str, str]]

    spec = PaginationSpec(("token",), ((("marker",), ("items", -1, "key")),))
    call = Call([Page(None, [{"key": "a"}, {"key": "b"}]), Page("c", []), Page(None, [])])
    assert len([page async for page in paginate(call, {}, spec)]) == 3
    assert call.requests == [{}, {"token": "b"}, {"token": "c"}]


@pytest.mark.asyncio
async def test_snapshot_protects_nested_cursor_from_consumer_mutation() -> None:
    cursor: dict[str, object] = {"pk": {"s": "original"}, "binary": b"bytes"}
    first = {"next_token": cursor}
    call = Call([first, {"next_token": None}])
    pages = paginate(call, {}, SINGLE)
    assert await anext(pages) is first
    cast(dict[str, object], cursor["pk"])["s"] = "changed"
    cursor.clear()
    await anext(pages)
    assert call.requests[1] == {"token": {"pk": {"s": "original"}, "binary": b"bytes"}}
    await pages.aclose()


@pytest.mark.asyncio
async def test_native_cursor_converts_only_continuation_state() -> None:
    class NativeCursor:
        def to_dict(self) -> dict[str, object]:
            return {"pk": {"s": "a"}}

    call = Call([{"next_token": NativeCursor()}, {"next_token": None}])
    assert len([page async for page in paginate(call, {}, SINGLE)]) == 2
    assert call.requests[1] == {"token": {"pk": {"s": "a"}}}


@pytest.mark.asyncio
async def test_break_does_not_request_another_page() -> None:
    call = Call([{"next_token": "a"}])
    async for _ in paginate(call, {}, SINGLE):
        break
    await asyncio.sleep(0)
    assert len(call.requests) == 1


@pytest.mark.asyncio
async def test_failure_propagates_without_paginator_retry() -> None:
    failure = RuntimeError("request failed")
    calls = 0

    async def fail(**params: object) -> object:
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(RuntimeError) as caught:
        await anext(paginate(fail, {}, SINGLE))
    assert caught.value is failure
    assert calls == 1


@pytest.mark.asyncio
async def test_cancellation_reaches_current_request() -> None:
    started = asyncio.Event()
    canceled = asyncio.Event()

    async def wait(**params: object) -> object:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    pages = paginate(wait, {}, SINGLE)
    pending = asyncio.create_task(anext(pages))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert canceled.is_set()


@pytest.mark.asyncio
async def test_iterators_keep_independent_request_state() -> None:
    calls: list[tuple[str, object]] = []

    async def request(*, group: str, token: object = None) -> object:
        calls.append((group, token))
        await asyncio.sleep(0)
        return {"next_token": group if token is None else None}

    async def consume(group: str) -> list[object]:
        return [page async for page in paginate(request, {"group": group}, SINGLE)]

    first, second = await asyncio.gather(consume("a"), consume("b"))
    assert len(first) == len(second) == 2
    assert calls == [("a", None), ("b", None), ("a", "a"), ("b", "b")]
