"""Compile model-checked waiter expressions to a small, native-object-safe IR."""

from __future__ import annotations

import keyword
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import jmespath

from .model import ServiceDescriptor, Waiter
from .smithy import SmithyMember, SmithyService, SmithyShape, short_name, to_snake_case

type Expression = tuple[str, str | int | float | bool | None, tuple[Expression, ...]]


@dataclass(frozen=True, slots=True)
class ResolvedAcceptor:
    state: str
    matcher: str
    expected: str | bool
    expression: Expression | None
    comparator: str | None
    path: str | None


@dataclass(frozen=True, slots=True)
class ResolvedWaiter:
    name: str
    operation: str
    delay: int
    acceptors: tuple[ResolvedAcceptor, ...]
    returns_output: bool
    returns_none: bool


@dataclass(frozen=True, slots=True)
class _Type:
    target: str
    dictionary: bool = False
    element: _Type | None = None


class _Compiler:
    def __init__(
        self, service: SmithyService,
        field_name: Callable[[SmithyShape, SmithyMember], str],
    ) -> None:
        self.service = service
        self.field_name = field_name

    def kind(self, value: _Type) -> str:
        if value.element is not None:
            return "list"
        if value.target.startswith("smithy.api#"):
            return short_name(value.target).removeprefix("Primitive").lower()
        shape = self.service.shapes[value.target]
        return "string" if shape.kind == "enum" else shape.kind

    def item(self, value: _Type) -> _Type:
        if value.element is not None:
            return value.element
        shape = self.service.shapes.get(value.target)
        if shape is None or shape.kind not in ("list", "set") or shape.member_target is None:
            raise ValueError("waiter projection/index requires a list")
        return _Type(shape.member_target, value.dictionary)

    def expected(self, value: _Type, expected: str) -> None:
        shape = self.service.shapes.get(value.target)
        if shape and shape.enum_values and expected not in shape.enum_values:
            raise ValueError(f"unknown waiter enum value: {shape.name}.{expected}")

    def compile(self, raw: object, context: _Type) -> tuple[Expression, _Type]:
        if not isinstance(raw, dict):
            raise ValueError("invalid waiter expression AST")
        node = cast(dict[str, object], raw)
        kind, value, children = node["type"], node.get("value"), node["children"]
        assert isinstance(kind, str) and isinstance(children, list)
        parts = cast(list[object], children)
        exprs: list[Expression] = []
        result = context
        if kind == "field":
            shape = self.service.shapes.get(context.target)
            if shape is None or shape.kind not in ("structure", "union"):
                raise ValueError(f"waiter field crosses a non-structure: {value}")
            member = next((m for m in shape.members if m.name == value), None)
            if member is None:
                raise ValueError(f"unknown waiter field: {shape.name}.{value}")
            dictionary = context.dictionary or shape.kind == "union"
            value = self.field_name(shape, member)
            if not dictionary and keyword.iskeyword(value):
                value += "_"
            result = _Type(member.target, dictionary)
        elif kind in ("subexpression", "index_expression"):
            for child in parts:
                expr, result = self.compile(child, result)
                exprs.append(expr)
        elif kind == "index":
            if type(value) is not int:
                raise ValueError("invalid waiter list index")
            result = self.item(context)
        elif kind == "identity":
            pass
        elif kind == "flatten":
            expr, collection = self.compile(parts[0], context)
            item = self.item(collection)
            # JMESPath [] flattens one level, not recursively.
            result = _Type("", element=self.item(item) if self.kind(item) == "list" else item)
            exprs.append(expr)
        elif kind in ("projection", "filter_projection"):
            expr, collection = self.compile(parts[0], context)
            item = self.item(collection)
            projected, result_item = self.compile(parts[1], item)
            exprs.extend((expr, projected))
            if kind == "filter_projection":
                predicate, predicate_type = self.compile(parts[2], item)
                if self.kind(predicate_type) != "boolean":
                    raise ValueError("waiter filter predicate must be boolean")
                exprs.append(predicate)
            result = _Type("", element=result_item)
        elif kind == "function_expression" and value == "length":
            if len(parts) != 1:
                raise ValueError("waiter length requires one argument")
            expr, argument = self.compile(parts[0], context)
            if self.kind(argument) not in ("list", "set", "map", "string"):
                raise ValueError("waiter length requires a collection or string")
            exprs.append(expr)
            result = _Type("smithy.api#Integer")
        elif kind == "literal":
            if type(value) is bool:
                result = _Type("smithy.api#Boolean")
            elif type(value) in (int, float):
                result = _Type("smithy.api#Double")
            elif isinstance(value, str):
                result = _Type("smithy.api#String")
            else:
                raise ValueError("unsupported waiter literal")
        elif kind in ("comparator", "and_expression", "or_expression"):
            types: list[_Type] = []
            for child in parts:
                expr, child_type = self.compile(child, context)
                exprs.append(expr)
                types.append(child_type)
            kinds = [self.kind(t) for t in types]
            if kind == "comparator":
                numeric = {"byte", "short", "integer", "long", "float", "double"}
                numbers = all(k in numeric for k in kinds)
                if value not in ("eq", "ne", "gt", "gte", "lt", "lte"):
                    raise ValueError("unsupported waiter comparison")
                if (not numbers and kinds != ["string", "string"]
                        and kinds != ["boolean", "boolean"]):
                    raise ValueError("incompatible waiter comparison types")
                if value not in ("eq", "ne") and not numbers:
                    raise ValueError("ordered waiter comparison requires numbers")
                for index in (0, 1):
                    if exprs[index][0] == "literal" and isinstance(exprs[index][1], str):
                        self.expected(types[1 - index], cast(str, exprs[index][1]))
            elif kinds != ["boolean", "boolean"]:
                raise ValueError("waiter logical expressions require booleans")
            result = _Type("smithy.api#Boolean")
        else:
            raise ValueError(f"unsupported waiter expression: {kind} {value}")
        assert value is None or isinstance(value, (str, int, float, bool))
        return (kind, value, tuple(exprs)), result


def _reject_streams(service: SmithyService, target: str, visited: set[str]) -> None:
    if target in visited or target not in service.shapes:
        return
    visited.add(target)
    shape = service.shapes[target]
    if shape.streaming or any(m.streaming for m in shape.members):
        raise ValueError("waiter polling outputs cannot own streams")
    for child in (
        *(m.target for m in shape.members), shape.member_target, shape.value_target,
    ):
        if child is not None:
            _reject_streams(service, child, visited)


def resolve_waiters(
    descriptor: ServiceDescriptor, service: SmithyService, native_operations: set[str],
    field_name: Callable[[SmithyShape, SmithyMember], str],
) -> tuple[ResolvedWaiter, ...]:
    definitions: dict[str, Waiter] = {}
    for modeled_operation in service.operations:
        for definition in modeled_operation.waiters:
            if definition.name in definitions:
                raise ValueError(f"duplicate waiter: {definition.name}")
            definitions[definition.name] = definition
    definitions.update((definition.name, definition) for definition in descriptor.waiters)
    operations = {op.name: op for op in service.operations}
    compiler = _Compiler(service, field_name)
    result: list[ResolvedWaiter] = []
    names: set[str] = set()
    for definition in sorted(definitions.values(), key=lambda item: item.name):
        name = to_snake_case(definition.name)
        if (not re.fullmatch(r"[A-Za-z]\w*", definition.name)
                or keyword.iskeyword(name) or name in names or name in ("names", "__init__")):
            raise ValueError(f"invalid/colliding waiter name: {definition.name}")
        names.add(name)
        operation = operations.get(definition.operation)
        if operation is None or operation.output_target is None:
            raise ValueError(f"unknown waiter polling operation/output: {definition.operation}")
        if operation.input_target is not None:
            if any(m.rust_name == "waiter_config"
                   for m in service.shapes[operation.input_target].members):
                raise ValueError("waiter_config collides with an operation parameter")
        _reject_streams(service, operation.output_target, set())
        context = _Type(operation.output_target, operation.rust_name not in native_operations)
        resolved: list[ResolvedAcceptor] = []
        returns_output = returns_none = False
        for acceptor in definition.acceptors:
            expression = None
            if acceptor.matcher == "output":
                assert acceptor.path is not None
                expression, value_type = compiler.compile(
                    jmespath.compile(acceptor.path).parsed, context,
                )
                comparator = acceptor.comparator
                if comparator in ("anyStringEquals", "allStringEquals"):
                    value_type = compiler.item(value_type)
                expected_kind = "boolean" if comparator == "booleanEquals" else "string"
                if compiler.kind(value_type) != expected_kind:
                    raise ValueError(f"incompatible waiter comparator: {definition.name}")
                if expected_kind == "string":
                    assert isinstance(acceptor.expected, str)
                    compiler.expected(value_type, acceptor.expected)
            elif acceptor.matcher == "errorType":
                if acceptor.expected not in {short_name(error) for error in operation.errors}:
                    raise ValueError(
                        f"unmodeled waiter error: {definition.name}.{acceptor.expected}",
                    )
            if acceptor.state == "success":
                if acceptor.matcher == "errorType" or (
                    acceptor.matcher == "success" and acceptor.expected is False
                ):
                    returns_none = True
                else:
                    returns_output = True
            resolved.append(ResolvedAcceptor(
                acceptor.state, acceptor.matcher, acceptor.expected,
                expression, acceptor.comparator, acceptor.path,
            ))
        result.append(ResolvedWaiter(
            name, operation.rust_name, definition.min_delay, tuple(resolved),
            returns_output, returns_none,
        ))
    return tuple(result)
