import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import FrozenInstanceError

import pytest
from rboto import (
    Interceptor,
    Next,
    OperationMetadata,
    OperationRequest,
    OperationResponse,
    Session,
    operation_context,
)
from rboto.exceptions import ClientError
from rboto.interceptors import invoke, resolve_interceptors


async def native(params: dict[str, object], metadata: dict[str, object]) -> object:
    await asyncio.sleep(0)
    metadata.update(
        region="us-west-2",
        attempt_count=2,
        status_code=200,
        request_id="request-2",
        attempts=[(1, 0.01, 503, "request-1"), (2, 0.02, 200, "request-2")],
    )
    return params["key"]


async def execute(
    interceptors: tuple[Interceptor, ...],
    call: Callable[[dict[str, object], dict[str, object]], Awaitable[object]] = native,
) -> object:
    return await invoke(
        call,
        {"key": "original"},
        interceptors,
        "s3",
        "GetObject",
        None,
        str,
        ("key", "optional"),
        ("key",),
    )


def test_middleware_nesting_mutation_and_response_replacement() -> None:
    async def run() -> None:
        order: list[str] = []
        task = asyncio.current_task()

        async def outer(request: OperationRequest, call_next: Next) -> OperationResponse:
            assert asyncio.current_task() is task
            order.append("outer:before")
            request.parameters["key"] = "changed"
            response = await call_next(request)
            order.append("outer:after")
            assert request.metadata.request_id == "request-2"
            response.output = f"wrapped:{response.output}"
            return response

        async def inner(request: OperationRequest, call_next: Next) -> OperationResponse:
            order.append("inner:before")
            assert request.parameters["key"] == "changed"
            response = await call_next(request)
            order.append("inner:after")
            return response

        assert await execute((outer, inner)) == "wrapped:changed"
        assert order == ["outer:before", "inner:before", "inner:after", "outer:after"]

    asyncio.run(run())


def test_with_parameters_preserves_context_and_metadata() -> None:
    async def run() -> None:
        async def change(request: OperationRequest, call_next: Next) -> OperationResponse:
            replacement = request.with_parameters({"key": "replacement"})
            assert request.parameters["key"] == "original"
            assert replacement.context is request.context
            result = await call_next(replacement)
            assert replacement.metadata is request.metadata
            return result

        assert await execute((change,)) == "replacement"

    asyncio.run(run())


def test_short_circuit_does_not_execute_downstream_or_native() -> None:
    async def run() -> None:
        async def cached(request: OperationRequest, call_next: Next) -> OperationResponse:
            assert not request.metadata.dispatched
            assert request.metadata.attempt_count == 0
            return OperationResponse("cached")

        async def forbidden(request: OperationRequest, call_next: Next) -> OperationResponse:
            pytest.fail("downstream middleware executed")

        assert await execute((cached, forbidden)) == "cached"

    asyncio.run(run())


def test_policy_exception_propagates_and_prevents_dispatch() -> None:
    async def run() -> None:
        error = PermissionError("blocked")

        async def deny(request: OperationRequest, call_next: Next) -> OperationResponse:
            raise error

        async def forbidden(params: dict[str, object], metadata: dict[str, object]) -> object:
            pytest.fail("native dispatch executed")

        with pytest.raises(PermissionError) as caught:
            await execute((deny,), forbidden)
        assert caught.value is error

    asyncio.run(run())


@pytest.mark.parametrize("recover", [False, True])
def test_modeled_error_can_propagate_or_be_recovered(recover: bool) -> None:
    async def run() -> None:
        error = ClientError("private detail", "AccessDenied", "request-id")

        async def fail(params: dict[str, object], metadata: dict[str, object]) -> object:
            metadata.update(attempt_count=1, status_code=403)
            raise error

        async def handle(request: OperationRequest, call_next: Next) -> OperationResponse:
            try:
                return await call_next(request)
            except ClientError as caught:
                assert caught is error
                assert request.metadata.error_code == "AccessDenied"
                assert request.metadata.request_id == "request-id"
                assert request.metadata.status_code == 403
                assert "private detail" not in repr(request.metadata)
                if recover:
                    return OperationResponse("fallback")
                raise

        if recover:
            assert await execute((handle,), fail) == "fallback"
        else:
            with pytest.raises(ClientError) as caught:
                await execute((handle,), fail)
            assert caught.value is error

    asyncio.run(run())


def test_postprocessing_error_propagates_to_outer_middleware() -> None:
    async def run() -> None:
        error = RuntimeError("postprocessing failed")

        async def broken(request: OperationRequest, call_next: Next) -> OperationResponse:
            await call_next(request)
            raise error

        async def outer(request: OperationRequest, call_next: Next) -> OperationResponse:
            with pytest.raises(RuntimeError) as caught:
                await call_next(request)
            assert caught.value is error
            return OperationResponse("recovered")

        assert await execute((outer, broken)) == "recovered"

    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["unknown", "missing", "null"])
def test_invalid_parameter_mutation_fails_before_native_dispatch(invalid: str) -> None:
    async def run() -> None:
        async def change(request: OperationRequest, call_next: Next) -> OperationResponse:
            if invalid == "unknown":
                request.parameters["typo"] = True
            elif invalid == "missing":
                del request.parameters["key"]
            else:
                request.parameters["key"] = None
            return await call_next(request)

        async def forbidden(params: dict[str, object], metadata: dict[str, object]) -> object:
            pytest.fail("invalid mutation reached native dispatch")

        with pytest.raises(TypeError, match="operation parameters"):
            await execute((change,), forbidden)

    asyncio.run(run())


def test_invalid_response_type_is_rejected() -> None:
    async def run() -> None:
        async def wrong(request: OperationRequest, call_next: Next) -> OperationResponse:
            return OperationResponse(42)

        with pytest.raises(TypeError, match="requires an output of type str"):
            await execute((wrong,))

    asyncio.run(run())


def test_each_continuation_can_be_called_only_once() -> None:
    async def run() -> None:
        calls = 0

        async def counted(params: dict[str, object], metadata: dict[str, object]) -> object:
            nonlocal calls
            calls += 1
            return "value"

        async def twice(request: OperationRequest, call_next: Next) -> OperationResponse:
            result = await call_next(request)
            with pytest.raises(RuntimeError, match="only be called once"):
                await call_next(request)
            return result

        assert await execute((twice,), counted) == "value"
        assert calls == 1

    asyncio.run(run())


def test_continuation_cannot_escape_its_interceptor() -> None:
    async def run() -> None:
        stored: list[tuple[OperationRequest, Next]] = []

        async def capture(request: OperationRequest, call_next: Next) -> OperationResponse:
            stored.append((request, call_next))
            return OperationResponse("cached")

        assert await execute((capture,)) == "cached"
        request, continuation = stored[0]
        with pytest.raises(RuntimeError, match="after its interceptor returns"):
            await continuation(request)

    asyncio.run(run())


def test_continuation_rejects_a_foreign_request() -> None:
    async def run() -> None:
        stored: list[OperationRequest] = []

        async def capture(request: OperationRequest, call_next: Next) -> OperationResponse:
            if stored:
                with pytest.raises(ValueError, match="current operation"):
                    await call_next(stored[0])
            stored.append(request)
            return OperationResponse("cached")

        assert await execute((capture,)) == "cached"
        assert await execute((capture,)) == "cached"

    asyncio.run(run())


def test_concurrent_context_isolation_and_metadata_immutability() -> None:
    async def run() -> None:
        observed: list[OperationRequest] = []

        async def capture(request: OperationRequest, call_next: Next) -> OperationResponse:
            observed.append(request)
            return await call_next(request)

        async def worker(name: str) -> None:
            with operation_context(correlation_id=name):
                await execute((capture,))

        with operation_context(correlation_id="outer"):
            await asyncio.gather(*(worker(str(i)) for i in range(30)))
            await execute((capture,))
        await execute((capture,))
        assert {r.context.correlation_id for r in observed} == {
            *(str(i) for i in range(30)),
            "outer",
            None,
        }
        assert len({r.context.operation_id for r in observed}) == 32
        for request in observed:
            assert request.metadata.retry_count == 1
            assert request.metadata.attempts[0].status_code == 503
            with pytest.raises(FrozenInstanceError):
                setattr(request.metadata, "status_code", 403)
            with pytest.raises(FrozenInstanceError):
                setattr(request.context, "operation_id", "other")

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["before", "native", "after"])
def test_cancellation_unwinds_the_chain(stage: str) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        finished: list[str] = []

        async def call(params: dict[str, object], metadata: dict[str, object]) -> object:
            if stage == "native":
                entered.set()
                await asyncio.Event().wait()
            return "result"

        async def inner(request: OperationRequest, call_next: Next) -> OperationResponse:
            try:
                if stage == "before":
                    entered.set()
                    await asyncio.Event().wait()
                result = await call_next(request)
                if stage == "after":
                    entered.set()
                    await asyncio.Event().wait()
                return result
            finally:
                finished.append("inner")

        async def outer(request: OperationRequest, call_next: Next) -> OperationResponse:
            try:
                return await call_next(request)
            finally:
                finished.append("outer")

        task = asyncio.create_task(execute((outer, inner), call))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == ["inner", "outer"]

    asyncio.run(run())


def test_late_native_metadata_cannot_change_cancelled_snapshot() -> None:
    async def run() -> None:
        raw: dict[str, object] = {}
        entered = asyncio.Event()
        snapshots: list[OperationMetadata] = []

        async def call(params: dict[str, object], metadata: dict[str, object]) -> object:
            nonlocal raw
            raw = metadata
            entered.set()
            await asyncio.Event().wait()
            return None

        async def capture(request: OperationRequest, call_next: Next) -> OperationResponse:
            try:
                return await call_next(request)
            finally:
                snapshots.append(request.metadata)

        task = asyncio.create_task(execute((capture,), call))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        raw.update(attempt_count=1, request_id="late")
        assert snapshots[0].attempt_count is None
        assert snapshots[0].error_type == "CancelledError"
        assert snapshots[0].request_id is None

    asyncio.run(run())


def test_configuration_snapshot_inheritance_and_explicit_disable() -> None:
    async def middleware(request: OperationRequest, call_next: Next) -> OperationResponse:
        return await call_next(request)

    mutable = [middleware]
    resolved = resolve_interceptors(mutable)
    mutable.clear()
    session = Session(interceptors=resolved)
    assert session.interceptors == (middleware,)
    assert resolve_interceptors(None, session.interceptors) == session.interceptors
    assert resolve_interceptors((), session.interceptors) == ()
    with pytest.raises(TypeError, match="callable"):
        resolve_interceptors((object(),))  # type: ignore[arg-type]


def test_continuation_cannot_run_in_a_detached_task() -> None:
    async def run() -> None:
        async def spawn(request: OperationRequest, call_next: Next) -> OperationResponse:
            async def detached() -> OperationResponse:
                return await call_next(request)

            with pytest.raises(RuntimeError, match="interceptor's task"):
                await asyncio.create_task(detached())
            # The rejected detached call did not use the continuation.
            return await call_next(request)

        assert await execute((spawn,)) == "original"

    asyncio.run(run())


def test_deadline_cancels_native_call_and_unwinds_middleware() -> None:
    async def run() -> None:
        cleaned: list[bool] = []

        async def slow(params: dict[str, object], metadata: dict[str, object]) -> object:
            try:
                await asyncio.Event().wait()
                return None
            finally:
                cleaned.append(True)

        async def deadline(request: OperationRequest, call_next: Next) -> OperationResponse:
            async with asyncio.timeout(0):
                return await call_next(request)

        with pytest.raises(TimeoutError):
            await execute((deadline,), slow)
        assert cleaned == [True]

    asyncio.run(run())
