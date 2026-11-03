from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from rboto._waiters import Acceptor, Expression, WaiterSpec, wait
from rboto.exceptions import ConnectionError, CredentialsError, ServiceError
from rboto.waiters import WaiterConfig, WaiterFailedError, WaiterTimeoutError

STATUS: Expression = ("field", "status", ())
SPEC = WaiterSpec("test.ready", 0, (
    Acceptor("success", "output", "READY", STATUS, "stringEquals"),
    Acceptor("failure", "output", "FAILED", STATUS, "stringEquals"),
    Acceptor("retry", "errorType", "NotFound"),
))


class Poll:
    def __init__(self, *responses: object) -> None:
        self.responses = iter(responses)
        self.requests: list[dict[str, object]] = []

    async def __call__(self, **parameters: object) -> object:
        self.requests.append(parameters)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.mark.asyncio
async def test_returns_original_output_and_never_converts_or_polls_again() -> None:
    class Native:
        status = "READY"

        def to_dict(self) -> dict[str, object]:
            raise AssertionError("must not materialize the response")

    output = Native()
    poll = Poll(output)
    assert await wait(poll, {"name": "table"}, SPEC, None) is output
    await asyncio.sleep(0)
    assert poll.requests == [{"name": "table"}]


@pytest.mark.asyncio
async def test_retry_missing_and_unknown_status_then_success() -> None:
    error = ServiceError("not created", "NotFound", "request-id", "Describe")
    output = SimpleNamespace(status="READY")
    poll = Poll(error, {}, {"status": None}, {"status": "FUTURE"}, output)
    assert await wait(poll, {"name": "table"}, SPEC, None) is output
    assert poll.requests == [{"name": "table"}] * 5


@pytest.mark.asyncio
async def test_error_success_returns_none_and_respects_acceptor_order() -> None:
    spec = WaiterSpec("gone", 0, (
        Acceptor("success", "errorType", "NotFound"),
        Acceptor("failure", "success", False),
    ))
    poll = Poll({}, ServiceError("gone", "NotFound"))
    assert await wait(poll, {}, spec, None) is None
    assert len(poll.requests) == 2


@pytest.mark.asyncio
async def test_terminal_state_diagnostics_do_not_convert_payload() -> None:
    poll = Poll({"status": "PENDING"}, {"status": "FAILED"})
    with pytest.raises(WaiterFailedError) as caught:
        await wait(poll, {}, SPEC, None)
    error = caught.value
    assert error.waiter_name == "test.ready" and error.attempts == 2
    assert error.last_attempt is not None
    assert error.last_attempt.acceptor_index == 1 and error.last_attempt.number == 2
    assert error.reason == "terminal_state"
    assert not hasattr(error, "response")
    await asyncio.sleep(0)
    assert len(poll.requests) == 2


@pytest.mark.asyncio
async def test_terminal_error_keeps_modeled_cause_and_request_id() -> None:
    original = ServiceError("denied", "Denied", "request-id")
    poll = Poll(original)
    spec = WaiterSpec("failed", 0, (Acceptor("failure", "errorType", "Denied"),))
    with pytest.raises(WaiterFailedError) as caught:
        await wait(poll, {}, spec, None)
    assert caught.value.__cause__ is original
    last = caught.value.last_attempt
    assert last and last.error_code == "Denied" and last.request_id == "request-id"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ServiceError("denied", "AccessDeniedException"),
    CredentialsError("no credentials"),
    ConnectionError("connection failed"),
    TimeoutError("operation deadline"),
    ValueError("interceptor failed"),
])
async def test_unmatched_failures_propagate_without_a_second_retry_layer(
    error: Exception,
) -> None:
    poll = Poll(error)
    with pytest.raises(type(error)) as caught:
        await wait(poll, {}, SPEC, WaiterConfig(timeout=1))
    assert caught.value is error
    assert len(poll.requests) == 1


@pytest.mark.asyncio
async def test_max_attempts_counts_first_poll_and_has_no_final_sleep() -> None:
    poll = Poll({}, ServiceError("not found", "NotFound", "last-id"))
    with pytest.raises(WaiterTimeoutError) as caught:
        await wait(poll, {}, SPEC, WaiterConfig(max_attempts=2, delay=0))
    error = caught.value
    assert error.reason == "max_attempts" and error.attempts == 2
    assert error.last_attempt and error.last_attempt.request_id == "last-id"
    # A long configured delay cannot delay exhaustion after the final poll.
    async with asyncio.timeout(1):
        with pytest.raises(WaiterTimeoutError):
            await wait(Poll({}), {}, SPEC, WaiterConfig(max_attempts=1, delay=3600))


@pytest.mark.asyncio
@pytest.mark.parametrize("during_request", [True, False])
async def test_deadline_interrupts_request_or_sleep_without_later_polls(
    during_request: bool,
) -> None:
    calls = 0
    finalized = asyncio.Event()

    async def call(**params: object) -> object:
        nonlocal calls
        calls += 1
        try:
            if during_request:
                await asyncio.Event().wait()
            return {}
        finally:
            finalized.set()

    async with asyncio.timeout(2):
        with pytest.raises(WaiterTimeoutError) as caught:
            await wait(call, {}, SPEC, WaiterConfig(delay=3600, timeout=0.02))
    assert calls == 1 and finalized.is_set()
    assert caught.value.reason == "deadline"
    assert (caught.value.last_attempt is None) == during_request
    await asyncio.sleep(0.03)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("during_request", [True, False])
async def test_external_cancellation_propagates_and_no_background_poll_remains(
    during_request: bool,
) -> None:
    called = asyncio.Event()
    finalized = asyncio.Event()
    calls = 0

    async def call(**params: object) -> object:
        nonlocal calls
        calls += 1
        called.set()
        try:
            if during_request:
                await asyncio.Event().wait()
            return {}
        finally:
            finalized.set()

    task = asyncio.create_task(wait(call, {}, SPEC, WaiterConfig(delay=3600)))
    await called.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finalized.is_set()
    await asyncio.sleep(0.01)
    assert calls == 1


@pytest.mark.asyncio
async def test_independent_concurrent_waiters_and_zero_delay_are_cooperative() -> None:
    counts: dict[str, int] = {}
    turns = 0

    async def call(**params: object) -> object:
        name = str(params["name"])
        counts[name] = counts.get(name, 0) + 1
        return {"status": "READY" if counts[name] == 10 else "PENDING", "name": name}

    async def ticker() -> None:
        nonlocal turns
        while turns < 10:
            turns += 1
            await asyncio.sleep(0)

    first, second, _ = await asyncio.gather(
        wait(call, {"name": "a"}, SPEC, WaiterConfig(delay=0)),
        wait(call, {"name": "b"}, SPEC, WaiterConfig(delay=0)), ticker(),
    )
    assert first == {"status": "READY", "name": "a"}
    assert second == {"status": "READY", "name": "b"}
    assert counts == {"a": 10, "b": 10} and turns == 10


@pytest.mark.asyncio
async def test_sync_completion_cannot_ignore_overall_deadline() -> None:
    # Simulate an interceptor that suppresses cancellation and returns late.
    async def call(**params: object) -> object:
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return {"status": "READY"}
        raise AssertionError("deadline did not fire")

    with pytest.raises(WaiterTimeoutError, match="deadline"):
        await wait(call, {}, SPEC, WaiterConfig(timeout=0.01))


@pytest.mark.asyncio
async def test_previous_output_is_released_before_next_poll() -> None:
    import weakref

    class Output:
        status = "PENDING"

    reference: weakref.ReferenceType[Output] | None = None

    async def call(**params: object) -> object:
        nonlocal reference
        if reference is not None:
            assert reference() is None
            return {"status": "READY"}
        value = Output()
        reference = weakref.ref(value)
        return value

    await wait(call, {}, SPEC, None)


@pytest.mark.parametrize("config", [
    lambda: WaiterConfig(delay=-1),
    lambda: WaiterConfig(delay=float("nan")),
    lambda: WaiterConfig(timeout=float("inf")),
    lambda: WaiterConfig(timeout=0),
    lambda: WaiterConfig(max_attempts=0),
    lambda: WaiterConfig(max_attempts=True),
    lambda: WaiterConfig(delay=True),
])
def test_invalid_polling_budgets_are_rejected(config: Callable[[], WaiterConfig]) -> None:
    with pytest.raises(ValueError):
        config()


@pytest.mark.asyncio
@pytest.mark.parametrize("comparator,expected,values", [
    ("anyStringEquals", "READY", [["PENDING"], [], ["PENDING", "READY"]]),
    ("allStringEquals", "READY", [[], ["READY", "PENDING"], ["READY", "READY"]]),
    ("booleanEquals", "false", [None, "false", False]),
])
async def test_collection_and_boolean_comparators(
    comparator: str, expected: str, values: list[object],
) -> None:
    spec = WaiterSpec("values", 0, (
        Acceptor("success", "output", expected, STATUS, comparator),
    ))
    poll = Poll(*[{"status": value} for value in values])
    assert await wait(poll, {}, spec, None) == {"status": values[-1]}
    assert len(poll.requests) == len(values)


@pytest.mark.asyncio
async def test_nested_outer_timeout_stays_owned_by_caller() -> None:
    async def call(**params: object) -> object:
        await asyncio.Event().wait()
        return None

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await wait(call, {}, SPEC, WaiterConfig(timeout=10))


@pytest.mark.asyncio
async def test_transport_error_does_not_match_service_error_success() -> None:
    error = ConnectionError("no connection", "NotFound")
    spec = WaiterSpec("absent", 0, (Acceptor("success", "success", False),))
    with pytest.raises(ConnectionError) as caught:
        await wait(Poll(error), {}, spec, None)
    assert caught.value is error
