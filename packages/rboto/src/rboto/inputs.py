"""Validation and conversion used by generated service input adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from typing import Generic, Literal, Protocol, TypeGuard, TypeVar, cast, runtime_checkable

_T = TypeVar("_T", covariant=True)
_Kind = Literal[
    "string", "integer", "float", "boolean", "blob", "unit", "document",
    "structure", "union", "sequence", "map",
]


class InputConversionError(ValueError):
    """A value cannot be converted to the requested service input type."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


@dataclass(frozen=True, slots=True)
class _Member:
    target: str
    required: bool = False


@dataclass(frozen=True, slots=True)
class _Shape:
    kind: _Kind
    members: Mapping[str, _Member] | None = None
    item: str | None = None
    key: str | None = None
    values: tuple[str, ...] = ()
    sparse: bool = False
    streaming: bool = False


_BUILTINS = {
    "String": _Shape("string"),
    "Timestamp": _Shape("string"),
    "Boolean": _Shape("boolean"),
    "Byte": _Shape("integer"),
    "Short": _Shape("integer"),
    "Integer": _Shape("integer"),
    "Long": _Shape("integer"),
    "BigInteger": _Shape("integer"),
    "Float": _Shape("float"),
    "Double": _Shape("float"),
    "BigDecimal": _Shape("float"),
    "Blob": _Shape("blob"),
    "Document": _Shape("document"),
    "Unit": _Shape("unit"),
}


@runtime_checkable
class _NativeValue(Protocol):
    def to_dict(self) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class _Schema:
    shapes: Mapping[str, _Shape]

    def shape(self, target: str) -> _Shape:
        if target.startswith("smithy.api#"):
            name = target.split("#", 1)[1].removeprefix("Primitive")
            if name in _BUILTINS:
                return _BUILTINS[name]
        if target in self.shapes:
            return self.shapes[target]
        raise ValueError(f"Missing input schema: {target}")

    def convert(
        self, target: str, value: object, path: str, active: set[int], *, event: bool = False,
    ) -> object:
        shape = self.shape(target)
        if shape.streaming and shape.kind == "union" and not event:
            if not isinstance(value, AsyncIterator):
                raise InputConversionError(path, "expected an async iterator")
            return self._events(target, cast(AsyncIterator[object], value), path)
        kind = shape.kind
        if kind == "string":
            if not isinstance(value, str):
                raise InputConversionError(path, "expected a string")
            if shape.values and value not in shape.values:
                raise InputConversionError(path, "unknown enum value")
            return value
        if kind == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise InputConversionError(path, "expected an integer")
            return value
        if kind == "float":
            if not isinstance(value, (float, int)) or isinstance(value, bool):
                raise InputConversionError(path, "expected a number")
            return value
        if kind == "boolean":
            if not isinstance(value, bool):
                raise InputConversionError(path, "expected a boolean")
            return value
        if kind == "blob":
            if not isinstance(value, bytes):
                raise InputConversionError(path, "expected bytes; read streams explicitly")
            return value
        if kind == "unit":
            if value is not None:
                raise InputConversionError(path, "expected None")
            return None
        if kind == "document" and (value is None or isinstance(value, (str, bool, int, float))):
            if isinstance(value, float) and not isfinite(value):
                raise InputConversionError(path, "document numbers must be finite")
            return value
        identity = id(value)
        if identity in active:
            raise InputConversionError(path, "cyclic input value")
        active.add(identity)
        try:
            return self._container(shape, value, path, active)
        finally:
            active.remove(identity)

    async def _events(
        self, target: str, values: AsyncIterator[object], path: str,
    ) -> AsyncIterator[object]:
        index = 0
        async for value in values:
            yield self.convert(target, value, f"{path}[{index}]", set(), event=True)
            index += 1

    def _container(self, shape: _Shape, value: object, path: str, active: set[int]) -> object:
        kind = shape.kind
        if kind == "structure" and isinstance(value, _NativeValue):
            if self._contains_stream(shape, set()):
                raise InputConversionError(
                    path, "use an explicit mapping for native values that may own streams",
                )
            value = value.to_dict()
        if kind in {"structure", "union", "map"} or (
            kind == "document" and isinstance(value, Mapping)
        ):
            if not isinstance(value, Mapping):
                raise InputConversionError(path, "expected a mapping")
            values = cast(Mapping[object, object], value)
            if kind in {"structure", "union"}:
                return self._structure(shape, values, path, active)
            key_target = shape.key or "smithy.api#String"
            item_target = shape.item or "smithy.api#Document"
            result: dict[object, object] = {}
            for index, (key, item) in enumerate(values.items()):
                item_path = f"{path}[{index}]"
                converted_key = self.convert(key_target, key, item_path + ".key", active)
                result[converted_key] = (
                    None if item is None and shape.sparse
                    else self.convert(item_target, item, item_path, active)
                )
            return result
        if kind in {"sequence", "document"}:
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
                raise InputConversionError(path, "expected a sequence")
            item_target = shape.item or "smithy.api#Document"
            return [
                None if item is None and shape.sparse
                else self.convert(item_target, item, f"{path}[{index}]", active)
                for index, item in enumerate(cast(Sequence[object], value))
            ]
        raise InputConversionError(path, "unsupported input value")

    def _contains_stream(self, shape: _Shape, visited: set[str]) -> bool:
        if shape.streaming:
            return True
        targets = [member.target for member in (shape.members or {}).values()]
        targets.extend(target for target in (shape.item, shape.key) if target is not None)
        for target in targets:
            if target not in visited:
                visited.add(target)
                if self._contains_stream(self.shape(target), visited):
                    return True
        return False

    def _structure(
        self, shape: _Shape, values: Mapping[object, object], path: str, active: set[int],
    ) -> dict[str, object]:
        members = shape.members or {}
        if shape.kind == "union" and len(values) != 1:
            raise InputConversionError(path, "expected exactly one known union variant")
        if any(key not in members for key in values):
            raise InputConversionError(path, "unexpected field or unknown union variant")
        result: dict[str, object] = {}
        for name, member in members.items():
            member_path = f"{path}.{name}"
            if name not in values:
                if member.required and shape.kind != "union":
                    raise InputConversionError(member_path, "missing required field")
                continue
            item = values[name]
            if item is None and self.shape(member.target).kind != "unit":
                if not member.required and shape.kind != "union":
                    continue
                raise InputConversionError(member_path, "required value is None")
            result[name] = self.convert(member.target, item, member_path, active)
        return result


@dataclass(frozen=True, slots=True)
class InputAdapter(Generic[_T]):
    """Convert a value to a generated input type, or raise InputConversionError."""

    _schema: _Schema = field(repr=False)
    _target: str

    def __call__(self, value: object) -> _T:
        # The generated schema describes _T. The recursive conversion validates
        # every field before crossing this single static typing boundary.
        return cast(
            _T, self._schema.convert(self._target, value, self._target.split("#")[-1], set()),
        )


class EnumInputAdapter(InputAdapter[_T]):
    """An input adapter that can also narrow an existing enum value."""

    __slots__ = ()

    def is_known(self, value: object) -> TypeGuard[_T]:
        """Return whether value belongs to this model's enum, without converting it."""
        return isinstance(value, str) and value in self._schema.shape(self._target).values
