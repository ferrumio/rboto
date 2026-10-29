"""Validate and resolve pagination metadata before rendering Python clients."""

from __future__ import annotations

import keyword
import re
from collections.abc import Callable
from dataclasses import dataclass

from .model import Pagination, ServiceDescriptor
from .smithy import SmithyMember, SmithyOperation, SmithyService, SmithyShape

Path = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class ResolvedPagination:
    operation: str
    input_tokens: tuple[str, ...]
    output_tokens: tuple[tuple[Path, ...], ...]
    more_results: Path | None
    page_size: str | None


def _path(expression: str) -> Path:
    result: list[str | int] = []
    for part in expression.strip().split("."):
        match = re.fullmatch(r"([A-Za-z_]\w*)(?:\[(-?\d+)\])?", part)
        if match is None:
            raise ValueError(f"unsupported pagination path: {expression}")
        result.append(match[1])
        if match[2] is not None:
            result.append(int(match[2]))
    return tuple(result)


def _kind(service: SmithyService, target: str) -> str:
    builtin = target.replace("smithy.api#Primitive", "smithy.api#")
    if builtin.startswith("smithy.api#"):
        return builtin.split("#", 1)[1].lower()
    shape = service.shapes[target]
    return "string" if shape.kind == "enum" else shape.kind


def _compatible(service: SmithyService, left: str, right: str) -> bool:
    if left == right:
        return True
    kind = _kind(service, left)
    return kind == _kind(service, right) and kind in {
        "string", "byte", "short", "integer", "long", "float", "double", "boolean", "blob",
    }


def _validate_cursor(service: SmithyService, target: str, visited: set[str]) -> None:
    if target in visited or target not in service.shapes:
        return
    visited.add(target)
    shape = service.shapes[target]
    if shape.streaming or any(member.streaming for member in shape.members):
        raise ValueError(f"pagination cursors cannot own streams: {target}")
    for child in (
        *[member.target for member in shape.members],
        shape.member_target, shape.key_target, shape.value_target,
    ):
        if child is not None:
            _validate_cursor(service, child, visited)


def resolve_pagination(
    descriptor: ServiceDescriptor,
    service: SmithyService,
    native_operations: set[str],
    field_name: Callable[[SmithyShape, SmithyMember], str],
) -> tuple[ResolvedPagination, ...]:
    overrides = dict(descriptor.pagination)
    unknown = overrides.keys() - {operation.name for operation in service.operations}
    if unknown:
        raise ValueError(f"pagination references unknown operations: {sorted(unknown)}")
    result: list[ResolvedPagination] = []
    for operation in service.operations:
        config = overrides.get(operation.name, operation.pagination)
        if config is not None:
            result.append(_resolve_operation(
                service, operation, config, operation.rust_name in native_operations, field_name,
            ))
    return tuple(result)


def _resolve_operation(
    service: SmithyService,
    operation: SmithyOperation,
    config: Pagination,
    native: bool,
    field_name: Callable[[SmithyShape, SmithyMember], str],
) -> ResolvedPagination:
    if not operation.input_target or not operation.output_target:
        raise ValueError(f"pagination requires input and output: {operation.name}")
    if not config.input_tokens or len(config.input_tokens) != len(config.output_tokens):
        raise ValueError(f"pagination token counts differ: {operation.name}")
    input_shape = service.shapes[operation.input_target]
    members = {member.name: member for member in input_shape.members}

    def input_member(name: str) -> SmithyMember:
        if name not in members:
            raise ValueError(f"unknown pagination input: {operation.name}.{name}")
        return members[name]

    def output_path(expression: str) -> tuple[Path, str]:
        target = operation.output_target
        assert target is not None
        dictionary = not native
        resolved: list[str | int] = []
        for step in _path(expression):
            shape = service.shapes.get(target)
            if shape is None:
                raise ValueError(f"pagination path crosses a scalar: {operation.name}.{expression}")
            if isinstance(step, int):
                if shape.kind not in {"list", "set"} or shape.member_target is None:
                    raise ValueError(f"pagination index requires a list: {expression}")
                resolved.append(step)
                target = shape.member_target
                continue
            if shape.kind not in {"structure", "union"}:
                raise ValueError(f"pagination field requires a structure: {expression}")
            member = next((member for member in shape.members if member.name == step), None)
            if member is None:
                raise ValueError(f"unknown pagination output: {operation.name}.{expression}")
            if member.streaming:
                raise ValueError(f"pagination cursors cannot own streams: {expression}")
            dictionary = dictionary or shape.kind == "union"
            name = field_name(shape, member)
            resolved.append(name + "_" if not dictionary and keyword.iskeyword(name) else name)
            target = member.target
        return tuple(resolved), target

    inputs: list[str] = []
    outputs: list[tuple[Path, ...]] = []
    for incoming, outgoing in zip(config.input_tokens, config.output_tokens, strict=True):
        member = input_member(incoming)
        if member.streaming:
            raise ValueError(f"pagination cursors cannot own streams: {incoming}")
        name = member.rust_name
        inputs.append(name + "_" if keyword.iskeyword(name) else name)
        _validate_cursor(service, member.target, set())
        alternatives: list[Path] = []
        for expression in outgoing.split("||"):
            path, target = output_path(expression)
            _validate_cursor(service, target, set())
            if not _compatible(service, member.target, target):
                raise ValueError(
                    f"incompatible pagination tokens: {operation.name}.{incoming} <- {expression}"
                )
            alternatives.append(path)
        outputs.append(tuple(alternatives))
    more_results = None
    if config.more_results:
        more_results, target = output_path(config.more_results)
        if _kind(service, target) != "boolean":
            raise ValueError(f"pagination more_results must be boolean: {operation.name}")
    page_size = None
    if config.page_size:
        size = input_member(config.page_size)
        if _kind(service, size.target) not in {"byte", "short", "integer", "long"}:
            raise ValueError(f"pagination page size must be an integer: {operation.name}")
        page_size = size.rust_name
    return ResolvedPagination(
        operation.rust_name, tuple(inputs), tuple(outputs), more_results, page_size,
    )
