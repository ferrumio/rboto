import asyncio
from dataclasses import FrozenInstanceError

import pytest
from rboto import OperationEvent, Session, operation_context
from rboto.exceptions import ClientError
from rboto.interceptors import invoke, resolve_interceptors


async def successful_call(params: dict[str, object], metadata: dict[str, object]) -> object:
    await asyncio.sleep(0)
    metadata.update(
        region="us-west-2",
        attempt_count=2,
        status_code=200,
        request_id="request-2",
        attempts=[(1, 0.01, 503, "request-1"), (2, 0.02, 200, "request-2")],
    )
    return params["result"]


def test_success_preserves_output_and_reports_immutable_metadata() -> None:
    async def run() -> None:
        events: list[OperationEvent] = []
        result = object()
        with operation_context(correlation_id="request"):
            actual = await invoke(
                successful_call, {"result": result}, (events.append,), "s3", "GetObject", None
            )
        assert actual is result
        start, end = events
        assert start.phase == "start" and end.phase == "success"
        assert start.context is end.context
        assert end.context.correlation_id == "request"
        assert start.attempt_count is None
        assert end.region == "us-west-2"
        assert end.attempt_count == 2 and end.retry_count == 1
        assert end.attempts[0].status_code == 503
        assert end.request_id == "request-2"
        assert end.duration is not None and end.duration >= 0
        with pytest.raises(FrozenInstanceError):
            setattr(end, "phase", "failure")
        with pytest.raises(FrozenInstanceError):
            setattr(end.context, "operation_id", "changed")
        with pytest.raises(FrozenInstanceError):
            setattr(end.attempts[0], "number", 5)
        assert "result" not in repr(end)

    asyncio.run(run())


def test_callback_order_async_support_and_same_task() -> None:
    async def run() -> None:
        order: list[str] = []
        caller = asyncio.current_task()

        async def first(event: OperationEvent) -> None:
            assert asyncio.current_task() is caller
            order.append(f"first:{event.phase}")
            await asyncio.sleep(0)

        def second(event: OperationEvent) -> None:
            assert asyncio.current_task() is caller
            order.append(f"second:{event.phase}")

        await invoke(successful_call, {"result": None}, (first, second), "s3", "ListBuckets", None)
        assert order == ["first:start", "second:start", "first:success", "second:success"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        ValueError("private input"),
        ClientError(message="private message", error_code="AccessDenied", request_id="request-id"),
    ],
)
def test_failures_preserve_exception_without_exposing_message(error: Exception) -> None:
    async def run() -> None:
        events: list[OperationEvent] = []

        async def fail(params: dict[str, object], metadata: dict[str, object]) -> object:
            raise error

        with pytest.raises(type(error)) as raised:
            await invoke(fail, {}, (events.append,), "s3", "ListBuckets", None)
        assert raised.value is error
        assert [event.phase for event in events] == ["start", "failure"]
        assert events[-1].error_type == type(error).__name__
        assert "private" not in repr(events)
        if isinstance(error, ClientError):
            assert events[-1].error_code == "AccessDenied"
            assert events[-1].request_id == "request-id"

    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_observer_failure_does_not_replace_result_or_skip_other_observers(failure: bool) -> None:
    async def run() -> None:
        reported: list[dict[str, object]] = []
        events: list[OperationEvent] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: reported.append(context)
        )

        def broken(event: OperationEvent) -> None:
            raise RuntimeError("observer")

        async def call(params: dict[str, object], metadata: dict[str, object]) -> int:
            if failure:
                raise ValueError("original")
            return 42

        if failure:
            with pytest.raises(ValueError, match="original"):
                await invoke(call, {}, (broken, events.append), "s3", "ListBuckets", None)
        else:
            assert await invoke(call, {}, (broken, events.append), "s3", "ListBuckets", None) == 42
        assert len(events) == 2 and len(reported) == 2
        assert all(context["message"] == "rboto interceptor failed" for context in reported)

    asyncio.run(run())


def test_concurrent_context_isolation_and_nested_context_restoration() -> None:
    async def run() -> None:
        events: list[OperationEvent] = []

        async def worker(name: str) -> None:
            with operation_context(correlation_id=name):
                await invoke(
                    successful_call, {"result": None}, (events.append,), "s3", "ListBuckets", None
                )

        with operation_context(correlation_id="outer"):
            await asyncio.gather(*(worker(str(i)) for i in range(30)))
            await invoke(
                successful_call, {"result": None}, (events.append,), "s3", "ListBuckets", None
            )
        await invoke(successful_call, {"result": None}, (events.append,), "s3", "ListBuckets", None)
        starts = [event for event in events if event.phase == "start"]
        assert {event.context.correlation_id for event in starts} == {
            *(str(i) for i in range(30)),
            "outer",
            None,
        }
        assert len({event.context.operation_id for event in starts}) == 32
        for start in starts:
            matching = [e for e in events if e.context.operation_id == start.context.operation_id]
            assert len(matching) == 2
            assert matching[0].context is matching[1].context

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["start", "operation", "terminal"])
def test_cancellation_delivers_exactly_one_terminal_event(stage: str) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        events: list[OperationEvent] = []

        async def observer(event: OperationEvent) -> None:
            if (stage == "start" and event.phase == "start") or (
                stage == "terminal" and event.phase == "success"
            ):
                entered.set()
                await asyncio.Event().wait()

        async def call(params: dict[str, object], metadata: dict[str, object]) -> None:
            if stage == "operation":
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(
            invoke(call, {}, (observer, events.append), "s3", "ListBuckets", None)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        terminal = [event for event in events if event.phase != "start"]
        assert len(terminal) == 1
        assert terminal[0].phase == ("success" if stage == "terminal" else "cancelled")
        assert not any(t is not asyncio.current_task() for t in asyncio.all_tasks())

    asyncio.run(run())


def test_callback_cancelled_error_without_task_cancellation_is_observer_failure() -> None:
    async def run() -> None:
        reported: list[dict[str, object]] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: reported.append(context)
        )

        def broken(event: OperationEvent) -> None:
            raise asyncio.CancelledError()

        assert (
            await invoke(successful_call, {"result": 3}, (broken,), "s3", "ListBuckets", None) == 3
        )
        assert len(reported) == 2

    asyncio.run(run())


def test_configuration_is_copied_and_explicit_empty_overrides_inheritance() -> None:
    events: list[OperationEvent] = []
    mutable = [events.append]
    resolved = resolve_interceptors(mutable)
    mutable.clear()
    session = Session(interceptors=resolved)
    assert session.interceptors == (events.append,)
    assert resolve_interceptors(None, session.interceptors) == session.interceptors
    assert resolve_interceptors((), session.interceptors) == ()
    with pytest.raises(FrozenInstanceError):
        setattr(session, "interceptors", ())
    with pytest.raises(TypeError, match="callable"):
        resolve_interceptors((object(),))  # type: ignore[arg-type]


def test_late_metadata_cannot_mutate_a_delivered_cancellation_event() -> None:
    async def run() -> None:
        sink: dict[str, object] = {}
        entered = asyncio.Event()
        events: list[OperationEvent] = []

        async def call(params: dict[str, object], metadata: dict[str, object]) -> None:
            nonlocal sink
            sink = metadata
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(invoke(call, {}, (events.append,), "s3", "ListBuckets", None))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        sink.update(attempt_count=1, request_id="late", attempts=[(1, 1.0, 200, "late")])
        assert len(events) == 2
        assert events[-1].attempt_count is None
        assert events[-1].request_id is None and not events[-1].attempts

    asyncio.run(run())
