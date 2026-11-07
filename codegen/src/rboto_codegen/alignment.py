from __future__ import annotations

from dataclasses import dataclass

from .model import AlignmentExtra, AlignmentType
from .rust_crate import RustCrate, RustField, RustType
from .rust_source import TypeRef, parse_type, tokens
from .smithy import SmithyMember, SmithyService, SmithyShape, to_snake_case


@dataclass(frozen=True, slots=True)
class OperationAlignment:
    name: str
    smithy_only_inputs: tuple[str, ...]
    rust_only_inputs: tuple[str, ...]
    smithy_only_outputs: tuple[str, ...]
    rust_only_outputs: tuple[str, ...]

    @property
    def aligned(self) -> bool:
        return not (
            self.smithy_only_inputs
            or self.rust_only_inputs
            or self.smithy_only_outputs
            or self.rust_only_outputs
        )


@dataclass(frozen=True, slots=True)
class AlignmentReport:
    aligned: tuple[OperationAlignment, ...]
    mismatched: tuple[OperationAlignment, ...]
    smithy_only_operations: tuple[str, ...]
    rust_only_operations: tuple[str, ...]
    semantic_mismatches: tuple[str, ...] = ()

    def require_aligned(self, service_id: str) -> None:
        problems = [
            *(f"{item.name}: {item}" for item in self.mismatched),
            *(f"missing Rust operation: {name}" for name in self.smithy_only_operations),
            *(f"unmodeled Rust operation: {name}" for name in self.rust_only_operations),
            *self.semantic_mismatches,
        ]
        if problems:
            raise ValueError(f"alignment failed for {service_id}:\n" + "\n".join(problems))


def align(
    service: SmithyService,
    crate: RustCrate,
    extras: tuple[AlignmentExtra, ...] = (),
    type_overrides: tuple[AlignmentType, ...] = (),
) -> AlignmentReport:
    smithy_operations = {operation.rust_name: operation for operation in service.operations}
    common = sorted(set(smithy_operations) & set(crate.operations))
    results: list[OperationAlignment] = []

    for name in common:
        smithy_operation = smithy_operations[name]
        rust_operation = crate.operations[name]

        def smithy_members(target: str | None) -> set[str]:
            if target is None or target == "smithy.api#Unit":
                return set()
            shape = service.shapes.get(target)
            if shape is None:
                raise ValueError(f"operation references unknown shape: {target}")
            return {member.rust_name for member in shape.members}

        smithy_inputs = smithy_members(smithy_operation.input_target)
        smithy_outputs = smithy_members(smithy_operation.output_target)
        rust_inputs = {field.name for field in rust_operation.input_fields}
        rust_outputs = {field.name for field in rust_operation.output_fields}
        results.append(
            OperationAlignment(
                name=name,
                smithy_only_inputs=tuple(sorted(smithy_inputs - rust_inputs)),
                rust_only_inputs=tuple(sorted(rust_inputs - smithy_inputs)),
                smithy_only_outputs=tuple(sorted(smithy_outputs - rust_outputs)),
                rust_only_outputs=tuple(sorted(rust_outputs - smithy_outputs)),
            )
        )

    semantics = _SemanticAlignment(service, crate, type_overrides)
    for extra in extras:
        extra_operation = crate.operations.get(extra.operation)
        model = smithy_operations.get(extra.operation)
        shape = service.shapes.get(model.output_target or "") if model else None
        field = (
            next(
                (field for field in extra_operation.output_fields if field.name == extra.member),
                None,
            )
            if extra_operation
            else None
        )
        if (
            shape is None
            or field is None
            or any(member.rust_name == extra.member for member in shape.members)
            or field.rust_type != extra.rust_type
            or field.optional != extra.optional
        ):
            semantics.problems.append(
                f"{extra.operation}.output.{extra.member}: stale or changed alignment extra"
            )
    for operation in service.operations:
        rust = crate.operations.get(operation.rust_name)
        if rust is None:
            continue
        semantics.fields(
            operation.input_target,
            rust.input_fields,
            f"{operation.rust_name}.input",
            operation=True,
        )
        semantics.fields(
            operation.output_target,
            rust.output_fields,
            f"{operation.rust_name}.output",
            operation=True,
        )
    models = {_normalized(shape.name): shape for shape in service.shapes.values()}
    for rust_type in crate.types.values():
        shape = models.get(_normalized(rust_type.name))
        if shape is None:
            if rust_type.name == "Unit" and rust_type.is_struct and not rust_type.fields:
                continue
            # Event error aggregates are not model shapes; their members are checked below.
            if rust_type.name in crate.event_errors and not rust_type.is_struct:
                continue
            semantics.problems.append(f"{rust_type.name}: Rust type has no modeled shape")
        elif rust_type.is_struct:
            semantics.fields(shape.shape_id, rust_type.fields, shape.name, rust_type=rust_type)
        elif shape.kind == "union":
            semantics.union(shape, rust_type)
        elif not shape.enum_values or rust_type.union:
            semantics.problems.append(f"{shape.name}: expected modeled enum, got incompatible type")
        elif set(rust_type.enum_values) != set(shape.enum_values):
            semantics.problems.append(f"{shape.name}: modeled and Rust enum values differ")
    for name, error_fields in crate.errors.items():
        shape = models.get(_normalized(name))
        if shape is None or not shape.error:
            semantics.problems.append(f"{name}: Rust error has no modeled error shape")
        else:
            semantics.fields(shape.shape_id, error_fields, name, error=True)
    for target in sorted(semantics.overrides.keys() - semantics.used_overrides):
        semantics.problems.append(f"{target}: unused alignment type override")

    # Extras are exact, documented SDK representations, never a general ignore list.
    approved = {(extra.operation, extra.member) for extra in extras}
    results = [
        OperationAlignment(
            item.name,
            item.smithy_only_inputs,
            item.rust_only_inputs,
            item.smithy_only_outputs,
            tuple(name for name in item.rust_only_outputs if (item.name, name) not in approved),
        )
        for item in results
    ]
    return AlignmentReport(
        aligned=tuple(result for result in results if result.aligned),
        mismatched=tuple(result for result in results if not result.aligned),
        smithy_only_operations=tuple(sorted(set(smithy_operations) - set(crate.operations))),
        rust_only_operations=tuple(sorted(set(crate.operations) - set(smithy_operations))),
        semantic_mismatches=tuple(semantics.problems),
    )


def _normalized(name: str) -> str:
    return name.replace("_", "").lower()


class _SemanticAlignment:
    def __init__(
        self,
        service: SmithyService,
        crate: RustCrate,
        overrides: tuple[AlignmentType, ...],
    ) -> None:
        self.service = service
        self.crate = crate
        self.problems: list[str] = []
        self.overrides = {item.shape: item for item in overrides}
        self.used_overrides: set[str] = set()
        for item in overrides:
            shape = service.shapes.get(item.shape)
            if shape is None or shape.kind != item.kind:
                self.problems.append(f"{item.shape}: stale or changed alignment type override")

    def fields(
        self,
        target: str | None,
        fields: tuple[RustField, ...],
        path: str,
        *,
        operation: bool = False,
        error: bool = False,
        rust_type: RustType | None = None,
    ) -> None:
        if target in {None, "smithy.api#Unit"}:
            return
        shape = self.service.shapes.get(target or "")
        if shape is None or shape.kind != "structure":
            self.problems.append(f"{path}: missing or non-structure model {target}")
            return
        # Match the generator's acronym/underscore normalization.
        members = {member.rust_name.replace("_", ""): member for member in shape.members}
        actual = {field.name.replace("_", ""): field for field in fields}
        if len(members) != len(shape.members) or len(actual) != len(fields):
            self.problems.append(f"{path}: ambiguous normalized field names")
        for name in sorted(members.keys() - actual.keys()):
            self.problems.append(f"{path}.{members[name].rust_name}: missing Rust field")
        if not operation:
            for name in sorted(actual.keys() - members.keys()):
                # SDK errors have a common message even when it is not modeled.
                if not (error and name == "message"):
                    self.problems.append(f"{path}.{actual[name].name}: unmodeled Rust field")
        for name in sorted(members.keys() & actual.keys()):
            member, field = members[name], actual[name]
            field_path = f"{path}.{field.name}"
            self.member_type(member.target, parse_type(tokens(field.rust_type)), field_path)
            target_shape = self.service.shapes.get(member.target)
            kind = target_shape.kind if target_shape else member.target.removeprefix("smithy.api#")
            if (
                operation
                and not (member.required or member.defaulted)
                and not field.optional
                and not (target_shape and target_shape.streaming)
            ):
                self.problems.append(f"{field_path}: optional modeled field lost Rust Option")
            # Required operation members can remain optional in the SDK's lenient
            # request/response containers; nested builders follow model defaults.
            if not operation and not error:
                expected_optional = member.client_optional or not (
                    member.required or member.defaulted
                )
                # Required structures/unions may be nullable in the SDK's client
                # representation, including recursive/boxed references.
                if not (member.required and kind in {"structure", "union"} and field.optional):
                    if field.optional != expected_optional:
                        self.problems.append(
                            f"{field_path}: optionality differs "
                            f"(model permits None={expected_optional}, "
                            f"Rust Option={field.optional})"
                        )
                if rust_type is not None:
                    required = (
                        member.required
                        and not member.defaulted
                        and not member.client_optional
                        and not field.optional
                    )
                    if (field.name in rust_type.builder_required_fields) != required:
                        self.problems.append(
                            f"{field_path}: builder requiredness differs from model"
                        )

    def member_type(self, target: str, actual: TypeRef, path: str) -> None:
        shape = self.service.shapes.get(target)
        kind = (
            shape.kind
            if shape
            else target.removeprefix("smithy.api#").removeprefix("Primitive").lower()
        )
        if target in self.overrides:
            override = self.overrides[target]
            self.used_overrides.add(target)
            if actual != parse_type(tokens(override.rust_type)):
                self.problems.append(
                    f"{path}: changed SDK representation for {target}: {actual.render()}"
                )
            return
        if actual.name == "::std::boxed::Box" and len(actual.arguments) == 1:
            self.member_type(target, actual.arguments[0], path)
            return
        primitives = {
            "string": "::std::string::String",
            "boolean": "bool",
            "byte": "i8",
            "short": "i16",
            "integer": "i32",
            "long": "i64",
            "float": "f32",
            "double": "f64",
            "timestamp": "::aws_smithy_types::DateTime",
            "blob": "::aws_smithy_types::Blob",
            "document": "::aws_smithy_types::Document",
        }
        expected = primitives.get(kind)
        if kind == "blob" and shape and shape.streaming:
            expected = "::aws_smithy_types::byte_stream::ByteStream"
        if shape and shape.enum_values or kind in {"structure", "union"}:
            if shape is None:
                self.problems.append(f"{path}: unknown model shape {target}")
                return
            if shape.streaming and kind == "union":
                if (
                    actual.name
                    not in {
                        "::aws_smithy_http::event_stream::EventStreamSender",
                        "crate::event_receiver::EventReceiver",
                    }
                    or len(actual.arguments) != 2
                ):
                    self.problems.append(f"{path}: expected event stream, got {actual.render()}")
                    return
                error_type = actual.arguments[1]
                variants = self.crate.event_errors.get(
                    error_type.name.removeprefix("crate::types::error::")
                )
                expected_errors = {
                    _normalized(self.service.shapes[member.target].name)
                    for member in shape.members
                    if member.target in self.service.shapes
                    and self.service.shapes[member.target].error
                }
                if (
                    variants is None
                    or error_type.arguments
                    or not error_type.name.startswith("crate::types::error::")
                    or {_normalized(error) for _, error in variants} != expected_errors
                ):
                    self.problems.append(
                        f"{path}: modeled event errors differ from SDK receiver/sender"
                    )
                actual = actual.arguments[0]
            name = actual.name.removeprefix("crate::types::")
            rust = self.crate.types.get(name)
            if (
                actual.arguments
                or not actual.name.startswith("crate::types::")
                or _normalized(name) != _normalized(shape.name)
                or rust is None
            ):
                self.problems.append(f"{path}: expected {shape.name}, got {actual.render()}")
            elif (kind == "structure") != rust.is_struct:
                self.problems.append(f"{path}: incompatible structure/enum/union representation")
            return
        if shape and kind in {"list", "set", "map"}:
            expected = "::std::collections::HashMap" if kind == "map" else "::std::vec::Vec"
            children = (
                (("key", shape.key_target), ("value", shape.value_target))
                if kind == "map"
                else (("member", shape.member_target),)
            )
            if actual.name != expected or len(actual.arguments) != len(children):
                self.problems.append(f"{path}: expected {expected}, got {actual.render()}")
                return
            for (label, child), argument in zip(children, actual.arguments, strict=True):
                sparse = shape.sparse and label != "key"
                optional = argument.name == "::std::option::Option"
                if sparse != optional or optional and len(argument.arguments) != 1:
                    self.problems.append(f"{path}.{label}: sparse collection optionality differs")
                    continue
                if child is None:
                    self.problems.append(f"{path}.{label}: missing collection target")
                else:
                    self.member_type(
                        child,
                        argument.arguments[0] if optional else argument,
                        f"{path}.{label}",
                    )
            return
        if expected is None or actual != TypeRef(expected):
            self.problems.append(f"{path}: expected {target} ({expected}), got {actual.render()}")

    def union(self, shape: SmithyShape, rust: RustType) -> None:
        variants = {to_snake_case(name).replace("_", ""): name for name in rust.enum_variants}
        payloads = dict(rust.variant_types)
        members: dict[str, SmithyMember] = {
            member.rust_name.replace("_", ""): member
            for member in shape.members
            if not (
                self.service.shapes.get(member.target) and self.service.shapes[member.target].error
            )
        }
        for name in sorted(members.keys() - variants.keys()):
            self.problems.append(f"{shape.name}.{members[name].name}: missing Rust union variant")
        for name in sorted(variants.keys() - members.keys() - {"unknown"}):
            self.problems.append(f"{shape.name}.{variants[name]}: unmodeled Rust union variant")
        for name in sorted(members.keys() & variants.keys()):
            member = members[name]
            payload = payloads.get(variants[name])
            if member.target == "smithy.api#Unit" and payload is None:
                continue
            if payload is None:
                self.problems.append(f"{shape.name}.{member.name}: missing union payload")
            else:
                self.member_type(
                    member.target, parse_type(tokens(payload)), f"{shape.name}.{member.name}"
                )
