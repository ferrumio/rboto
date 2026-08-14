from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .model import ServiceDescriptor


@dataclass(frozen=True, slots=True)
class SmithyMember:
    name: str
    rust_name: str
    target: str
    required: bool
    streaming: bool
    documentation: str


@dataclass(frozen=True, slots=True)
class SmithyShape:
    shape_id: str
    name: str
    kind: str
    members: tuple[SmithyMember, ...]
    member_target: str | None
    key_target: str | None
    value_target: str | None
    enum_values: tuple[str, ...]
    streaming: bool
    error: bool


@dataclass(frozen=True, slots=True)
class SmithyOperation:
    shape_id: str
    name: str
    rust_name: str
    input_target: str | None
    output_target: str | None
    errors: tuple[str, ...]
    documentation: str


@dataclass(frozen=True, slots=True)
class SmithyService:
    shape_id: str
    name: str
    operations: tuple[SmithyOperation, ...]
    shapes: dict[str, SmithyShape]


def to_snake_case(name: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).lower()


def short_name(shape_id: str) -> str:
    return shape_id.rsplit("#", 1)[-1]


def _dict(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"expected object at {context}")
    return cast(dict[str, object], value)


def _string(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"expected string at {context}")
    return value


def _traits(value: object) -> dict[str, object]:
    if value is None:
        return {}
    return _dict(value, "traits")


def fetch_model(descriptor: ServiceDescriptor, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(descriptor.model_url, timeout=120).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != descriptor.model_sha256:
        raise ValueError(
            f"model hash mismatch for {descriptor.service_id}: "
            f"expected {descriptor.model_sha256}, got {digest}"
        )
    destination.write_bytes(data)
    return destination


def verify_model(path: Path, descriptor: ServiceDescriptor) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != descriptor.model_sha256:
        raise ValueError(
            f"model hash mismatch for {path}: expected {descriptor.model_sha256}, got {digest}"
        )


def load_model(path: Path, descriptor: ServiceDescriptor) -> SmithyService:
    verify_model(path, descriptor)
    root = _dict(json.loads(path.read_text()), "root")
    raw_shapes = _dict(root.get("shapes"), "shapes")
    shapes: dict[str, SmithyShape] = {}

    for shape_id, raw_shape_value in raw_shapes.items():
        raw_shape = _dict(raw_shape_value, shape_id)
        kind = _string(raw_shape.get("type"), f"{shape_id}.type")
        traits = _traits(raw_shape.get("traits"))
        members: list[SmithyMember] = []
        for member_name, raw_member_value in _dict(
            raw_shape.get("members", {}), f"{shape_id}.members"
        ).items():
            raw_member = _dict(raw_member_value, f"{shape_id}.{member_name}")
            member_traits = _traits(raw_member.get("traits"))
            members.append(
                SmithyMember(
                    name=member_name,
                    rust_name=to_snake_case(member_name),
                    target=_string(
                        raw_member.get("target"), f"{shape_id}.{member_name}.target"
                    ),
                    required="smithy.api#required" in member_traits,
                    streaming="smithy.api#streaming" in member_traits,
                    documentation=str(
                        member_traits.get("smithy.api#documentation", "")
                    ),
                )
            )

        member_target: str | None = None
        if kind in {"list", "set"}:
            member_target = _string(
                _dict(raw_shape.get("member"), f"{shape_id}.member").get("target"),
                f"{shape_id}.member.target",
            )

        key_target: str | None = None
        value_target: str | None = None
        if kind == "map":
            key_target = _string(
                _dict(raw_shape.get("key"), f"{shape_id}.key").get("target"),
                f"{shape_id}.key.target",
            )
            value_target = _string(
                _dict(raw_shape.get("value"), f"{shape_id}.value").get("target"),
                f"{shape_id}.value.target",
            )

        enum_values: list[str] = []
        if kind == "enum":
            for member_name, raw_member_value in _dict(
                raw_shape.get("members", {}), f"{shape_id}.members"
            ).items():
                raw_member = _dict(raw_member_value, f"{shape_id}.{member_name}")
                member_traits = _traits(raw_member.get("traits"))
                enum_values.append(
                    _string(
                        member_traits.get("smithy.api#enumValue"),
                        f"{shape_id}.{member_name}.enumValue",
                    )
                )
        raw_enum = traits.get("smithy.api#enum")
        if isinstance(raw_enum, list):
            for index, raw_variant_value in enumerate(cast(list[object], raw_enum)):
                raw_variant = _dict(raw_variant_value, f"{shape_id}.enum[{index}]")
                enum_values.append(
                    _string(raw_variant.get("value"), f"{shape_id}.enum[{index}].value")
                )

        shapes[shape_id] = SmithyShape(
            shape_id=shape_id,
            name=short_name(shape_id),
            kind=kind,
            members=tuple(members),
            member_target=member_target,
            key_target=key_target,
            value_target=value_target,
            enum_values=tuple(enum_values),
            streaming="smithy.api#streaming" in traits,
            error="smithy.api#error" in traits,
        )

    service_entries = [shape for shape in shapes.values() if shape.kind == "service"]
    if len(service_entries) != 1:
        raise ValueError(f"expected one service shape, found {len(service_entries)}")
    service_shape = service_entries[0]
    raw_service = _dict(raw_shapes[service_shape.shape_id], service_shape.shape_id)
    raw_operations = raw_service.get("operations")
    if not isinstance(raw_operations, list):
        raise ValueError("service operations must be a list")

    operations: list[SmithyOperation] = []
    for raw_reference_value in cast(list[object], raw_operations):
        raw_reference = _dict(raw_reference_value, "service operation")
        operation_id = _string(raw_reference.get("target"), "operation target")
        raw_operation = _dict(raw_shapes.get(operation_id), operation_id)
        if raw_operation.get("type") != "operation":
            raise ValueError(f"{operation_id} is not an operation")

        def target(name: str) -> str | None:
            value = raw_operation.get(name)
            if value is None:
                return None
            return _string(_dict(value, f"{operation_id}.{name}").get("target"), name)

        raw_errors = raw_operation.get("errors", [])
        if not isinstance(raw_errors, list):
            raise ValueError(f"{operation_id}.errors must be a list")
        errors = tuple(
            _string(_dict(error, f"{operation_id}.error").get("target"), "error target")
            for error in cast(list[object], raw_errors)
        )
        operation_traits = _traits(raw_operation.get("traits"))
        name = short_name(operation_id)
        operations.append(
            SmithyOperation(
                shape_id=operation_id,
                name=name,
                rust_name=to_snake_case(name),
                input_target=target("input"),
                output_target=target("output"),
                errors=errors,
                documentation=str(operation_traits.get("smithy.api#documentation", "")),
            )
        )

    return SmithyService(
        shape_id=service_shape.shape_id,
        name=service_shape.name,
        operations=tuple(operations),
        shapes=shapes,
    )
