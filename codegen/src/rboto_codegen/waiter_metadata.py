"""Parse Smithy waitable traits and descriptor supplements with the same schema."""

from __future__ import annotations

from typing import cast

from .model import Waiter, WaiterAcceptor


def _table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("waiter metadata must be an object")
    return cast(dict[str, object], value)


def parse_waiters(value: object, operation: str | None = None) -> tuple[Waiter, ...]:
    result: list[Waiter] = []
    for name, raw in _table(value).items():
        spec = _table(raw)
        allowed = {"acceptors", "minDelay", "maxDelay", "documentation", "tags", "deprecated"}
        if operation is None:
            allowed.add("operation")
        if spec.keys() - allowed:
            raise ValueError(f"unknown waiter fields: {name}: {sorted(spec.keys() - allowed)}")
        op = operation if operation is not None else spec.get("operation")
        if not isinstance(op, str) or not op:
            raise ValueError(f"waiter {name} requires an operation")
        low, high = spec.get("minDelay", 2), spec.get("maxDelay", 120)
        if type(low) is not int or type(high) is not int or low <= 0 or high < low:
            raise ValueError(f"invalid waiter delays: {name}")
        documentation = spec.get("documentation", "")
        if not isinstance(documentation, str):
            raise ValueError(f"invalid waiter documentation: {name}")
        acceptors = spec.get("acceptors")
        if not isinstance(acceptors, list) or not acceptors:
            raise ValueError(f"waiter {name} needs acceptors")
        parsed: list[WaiterAcceptor] = []
        for raw_acceptor in cast(list[object], acceptors):
            entry = _table(raw_acceptor)
            state = entry.get("state")
            if entry.keys() != {"state", "matcher"} or state not in (
                "success", "retry", "failure",
            ):
                raise ValueError(f"invalid waiter acceptor: {name}")
            matcher = _table(entry["matcher"])
            if len(matcher) != 1:
                raise ValueError(f"waiter acceptor needs exactly one matcher: {name}")
            kind, expected = next(iter(matcher.items()))
            path = comparator = None
            if kind == "output":
                output = _table(expected)
                if output.keys() != {"path", "expected", "comparator"}:
                    raise ValueError(f"invalid waiter output matcher: {name}")
                path, comparator, expected = (
                    output["path"], output["comparator"], output["expected"],
                )
                if not isinstance(path, str) or not path or comparator not in (
                    "stringEquals", "booleanEquals", "anyStringEquals", "allStringEquals",
                ):
                    raise ValueError(f"invalid waiter path/comparator: {name}")
                if not isinstance(expected, str):
                    raise ValueError(f"waiter expected value must be a string: {name}")
                if comparator == "booleanEquals" and expected not in ("true", "false"):
                    raise ValueError(f"waiter boolean expected value must be true/false: {name}")
            elif kind == "errorType":
                if not isinstance(expected, str) or not expected:
                    raise ValueError(f"invalid waiter errorType: {name}")
            elif kind == "success":
                if type(expected) is not bool:
                    raise ValueError(f"invalid waiter success matcher: {name}")
            else:
                raise ValueError(f"unsupported waiter matcher: {name}.{kind}")
            assert isinstance(state, str)
            assert isinstance(expected, (str, bool))
            assert path is None or isinstance(path, str)
            assert comparator is None or isinstance(comparator, str)
            parsed.append(WaiterAcceptor(state, kind, expected, path, comparator))
        if not any(item.state == "success" for item in parsed):
            raise ValueError(f"waiter needs a success acceptor: {name}")
        result.append(Waiter(name, op, tuple(parsed), low, high, documentation))
    return tuple(result)
