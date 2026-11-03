"""Shared cooperative poller. Expressions read only model-validated output fields."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence, Sized
from dataclasses import dataclass
from typing import TypeVar, cast

from .exceptions import ServiceError
from .waiters import WaiterAttempt, WaiterConfig, WaiterFailedError, WaiterTimeoutError

_T = TypeVar("_T")
type Expression = tuple[str, str | int | float | bool | None, tuple[Expression, ...]]
_MISSING = object()


@dataclass(frozen=True, slots=True)
class Acceptor:
    state: str
    matcher: str
    expected: str | bool
    expression: Expression | None = None
    comparator: str | None = None


@dataclass(frozen=True, slots=True)
class WaiterSpec:
    name: str
    delay: float
    acceptors: tuple[Acceptor, ...]


def _items(value: object) -> Sequence[object] | None:
    if isinstance(value, (list, tuple)):
        return cast(Sequence[object], value)
    return None


def _evaluate(expression: Expression, context: object) -> object:
    kind, value, children = expression
    if kind == "field":
        assert isinstance(value, str)
        if isinstance(context, Mapping):
            result = cast(Mapping[str, object], context).get(value, _MISSING)
        else:
            result = getattr(context, value, _MISSING)
        return _MISSING if result is None else result
    if kind in ("subexpression", "index_expression"):
        for child in children:
            context = _evaluate(child, context)
            if context is _MISSING:
                break
        return context
    if kind == "index":
        items = _items(context)
        assert isinstance(value, int)
        return items[value] if items is not None and -len(items) <= value < len(items) else _MISSING
    if kind == "identity":
        return context
    if kind == "literal":
        return value
    if kind == "flatten":
        items = _items(_evaluate(children[0], context))
        if items is None:
            return _MISSING
        return [child for item in items for child in (
            cast(Sequence[object], item) if isinstance(item, (list, tuple)) else (item,)
        )]
    if kind in ("projection", "filter_projection"):
        items = _items(_evaluate(children[0], context))
        if items is None:
            return _MISSING
        result_items: list[object] = []
        for item in items:
            if kind == "filter_projection" and _evaluate(children[2], item) is not True:
                continue
            result = _evaluate(children[1], item)
            if result is not _MISSING:
                result_items.append(result)
        return result_items
    if kind == "function_expression":
        argument = _evaluate(children[0], context)
        return len(argument) if isinstance(argument, Sized) else _MISSING
    left = _evaluate(children[0], context)
    if kind == "and_expression" and left is False:
        return False
    if kind == "or_expression" and left is True:
        return True
    right = _evaluate(children[1], context)
    if kind == "and_expression" and right is False:
        return False
    if kind == "or_expression" and right is True:
        return True
    if left is _MISSING or right is _MISSING:
        return _MISSING
    if kind == "and_expression":
        return left is True and right is True
    if kind == "or_expression":
        return left is True or right is True
    if value == "eq":
        return left == right
    if value == "ne":
        return left != right
    if type(left) not in (int, float) or type(right) not in (int, float):
        return _MISSING
    lhs, rhs = cast(int | float, left), cast(int | float, right)
    return {"gt": lhs > rhs, "gte": lhs >= rhs, "lt": lhs < rhs, "lte": lhs <= rhs}[str(value)]


def _matches(acceptor: Acceptor, output: object, error: ServiceError | None) -> bool:
    if acceptor.matcher == "errorType":
        return error is not None and error.error_code == acceptor.expected
    if acceptor.matcher == "success":
        return (error is None) is acceptor.expected
    if error is not None:
        return False
    assert acceptor.expression is not None
    value = _evaluate(acceptor.expression, output)
    if value is _MISSING:
        return False
    if acceptor.comparator == "booleanEquals":
        return value is (acceptor.expected == "true")
    if acceptor.comparator == "stringEquals":
        return isinstance(value, str) and value == acceptor.expected
    items = _items(value)
    if not items:
        return False
    matches = (isinstance(item, str) and item == acceptor.expected for item in items)
    return any(matches) if acceptor.comparator == "anyStringEquals" else all(matches)


async def wait(
    call: Callable[..., Awaitable[_T]], params: dict[str, object],
    spec: WaiterSpec, config: WaiterConfig | None,
) -> _T | None:
    settings = config or WaiterConfig()
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + settings.timeout if settings.timeout is not None else None
    delay = spec.delay if settings.delay is None else settings.delay
    attempts = 0
    last_attempt: WaiterAttempt | None = None
    timer = asyncio.timeout_at(deadline)
    try:
        async with timer:
            while attempts < settings.max_attempts:
                # Also check synchronously: an interceptor can complete without yielding.
                if deadline is not None and loop.time() >= deadline:
                    raise WaiterTimeoutError(spec.name, attempts, last_attempt, "deadline")
                attempts += 1
                error: ServiceError | None = None
                output: _T | None = None
                try:
                    output = await call(**params)
                except ServiceError as caught:
                    error = caught
                if deadline is not None and loop.time() >= deadline:
                    output = None
                    raise WaiterTimeoutError(spec.name, attempts, last_attempt, "deadline")
                index = next((i for i, acceptor in enumerate(spec.acceptors)
                              if _matches(acceptor, output, error)), None)
                last_attempt = WaiterAttempt(
                    attempts, loop.time() - started, index,
                    error.error_code if error else None, error.request_id if error else None,
                )
                if index is None:
                    if error is not None:
                        raise error
                else:
                    state = spec.acceptors[index].state
                    if state == "success":
                        return output
                    if state == "failure":
                        output = None
                        raise WaiterFailedError(
                            spec.name, attempts, last_attempt, "terminal_state",
                        ) from error
                # Release the previous native response before sleeping or polling again.
                output = None
                error = None
                if attempts == settings.max_attempts:
                    raise WaiterTimeoutError(spec.name, attempts, last_attempt, "max_attempts")
                await asyncio.sleep(delay)
    except TimeoutError:
        if not timer.expired():
            raise
        raise WaiterTimeoutError(spec.name, attempts, last_attempt, "deadline") from None
    raise AssertionError("unreachable waiter state")
