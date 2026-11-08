from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .model import ServiceDescriptor
from .rust_source import (
    RustParseError,
    builder,
    closing,
    declarations,
    enum_values,
    enum_variants,
    parse_type,
    split_items,
    tokens,
    without_attributes,
)


@dataclass(frozen=True, slots=True)
class RustField:
    name: str
    rust_name: str
    rust_type: str
    optional: bool


@dataclass(frozen=True, slots=True)
class RustOperation:
    name: str
    input_fields: tuple[RustField, ...]
    output_fields: tuple[RustField, ...]
    output_type: str | None


@dataclass(frozen=True, slots=True)
class RustType:
    name: str
    fields: tuple[RustField, ...]
    enum_variants: tuple[str, ...]
    union: bool
    build_fallible: bool
    is_struct: bool
    variant_types: tuple[tuple[str, str], ...] = ()
    builder_required_fields: tuple[str, ...] = ()
    enum_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RustCrate:
    path: Path
    operations: dict[str, RustOperation]
    types: dict[str, RustType]
    errors: dict[str, tuple[RustField, ...]] = field(
        default_factory=lambda: dict[str, tuple[RustField, ...]](),
    )
    event_errors: dict[str, tuple[tuple[str, str], ...]] = field(
        default_factory=lambda: dict[str, tuple[tuple[str, str], ...]](),
    )


def parse_error_variants(content: str) -> dict[str, tuple[tuple[str, str], ...]]:
    """Read modeled variants only; reject unknown layouts instead of losing errors."""
    result: dict[str, tuple[tuple[str, str], ...]] = {}
    for declaration in declarations(tokens(content), "enum"):
        modeled: list[tuple[str, str]] = []
        for name, payload in enum_variants(declaration):
            if name == "Unhandled":
                if payload is None or payload.render() not in {
                    "crate::error::Unhandled",
                    "crate::error::sealed_unhandled::Unhandled",
                }:
                    raise RustParseError(f"{declaration.name}: unsupported Unhandled payload")
                continue
            if (
                payload is None
                or payload.arguments
                or not payload.name.startswith("crate::types::error::")
            ):
                raise RustParseError(
                    f"{declaration.name}.{name}: unsupported modeled error payload"
                )
            modeled.append((name, payload.name.rsplit("::", 1)[-1]))
        result[declaration.name] = tuple(modeled)
    return result


def find_crate(descriptor: ServiceDescriptor) -> Path:
    cargo_home = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo"))).expanduser()
    registry = cargo_home / "registry" / "src"
    expected = f"{descriptor.rust_crate}-{descriptor.rust_crate_version}"
    if registry.is_dir():
        for index in registry.iterdir():
            candidate = index / expected
            if candidate.is_dir():
                return candidate
    raise FileNotFoundError(f"{expected} is not available in the Cargo registry; run cargo fetch")


def _fields(parts: tuple[str, ...]) -> tuple[RustField, ...]:
    structs = declarations(parts, "struct")
    if not structs:
        raise RustParseError("expected a public named-field struct")
    declaration = structs[0]
    fields: list[RustField] = []
    for item in split_items(declaration.body):
        item = without_attributes(item)
        if not item:
            raise RustParseError(f"{declaration.name}: empty field")
        if item[0] != "pub":
            # SDK request metadata and builder state are intentionally private.
            if len(item) < 3 or item[1] != ":":
                raise RustParseError(f"{declaration.name}: unsupported field {' '.join(item)}")
            continue
        if item[1:2] == ("(",):
            closing(item, 1)
            continue
        if len(item) < 4 or item[2] != ":":
            raise RustParseError(f"{declaration.name}: unsupported public field {' '.join(item)}")
        rust_name = item[1]
        name = rust_name.removeprefix("r#")
        reference = parse_type(item[3:])
        optional = reference.name == "::std::option::Option"
        if optional:
            if len(reference.arguments) != 1:
                raise RustParseError(f"{declaration.name}.{name}: Option must have one argument")
            reference = reference.arguments[0]
        if name in {field.name for field in fields}:
            raise RustParseError(f"{declaration.name}: duplicate field {name}")
        fields.append(RustField(name, rust_name, reference.render(), optional))
    return tuple(fields)


def parse_fields(path: Path) -> tuple[RustField, ...]:
    try:
        return _fields(tokens(path.read_text()))
    except (OSError, RustParseError) as error:
        raise RustParseError(f"{path}: {error}") from error


def parse_struct_name(path: Path) -> str | None:
    try:
        structs = declarations(tokens(path.read_text()), "struct")
        return structs[0].name if structs else None
    except (OSError, RustParseError) as error:
        raise RustParseError(f"{path}: {error}") from error


def parse_builder_required_fields(content: str) -> tuple[str, ...]:
    parsed = builder(tokens(content))
    return parsed[1] if parsed else ()


def _operation(path: Path, name: str) -> RustOperation:
    input_path = path / f"_{name}_input.rs"
    output_path = path / f"_{name}_output.rs"
    # Both files are required even for operations with empty modeled structures.
    return RustOperation(
        name,
        parse_fields(input_path),
        parse_fields(output_path),
        parse_struct_name(output_path),
    )


def _type(path: Path) -> RustType | None:
    parts = tokens(path.read_text())
    structs = declarations(parts, "struct")
    if structs:
        name = structs[0].name
        parsed_builder = builder(parts, expected_type=name)
        if parsed_builder is None:
            raise RustParseError(f"{name}: missing public builder build method")
        fallible, required = parsed_builder
        fields = _fields(parts)
        unknown = set(required) - {field.name for field in fields}
        if unknown:
            raise RustParseError(
                f"{name}: builder references unknown required fields {sorted(unknown)}"
            )
        return RustType(
            name,
            fields,
            (),
            False,
            fallible,
            True,
            builder_required_fields=required,
        )
    enums = declarations(parts, "enum")
    if enums:
        declaration = enums[0]
        variants = enum_variants(declaration)
        payloads = tuple(
            (name, payload.render())
            for name, payload in variants
            if payload is not None and name != "Unknown"
        )
        return RustType(
            declaration.name,
            (),
            tuple(name for name, _ in variants),
            bool(payloads),
            False,
            False,
            payloads,
            enum_values=enum_values(parts, declaration) if not payloads else (),
        )
    if path.name != "error.rs":
        raise RustParseError("expected a public modeled struct or enum")
    return None


def parse_crate(descriptor: ServiceDescriptor) -> RustCrate:
    path = find_crate(descriptor)
    operations: dict[str, RustOperation] = {}
    for candidate in sorted((path / "src/operation").iterdir()):
        if candidate.is_dir():
            operations[candidate.name] = _operation(candidate, candidate.name)

    types: dict[str, RustType] = {}
    for type_path in sorted((path / "src/types").glob("*.rs")):
        if type_path.name == "builders.rs":
            continue
        try:
            rust_type = _type(type_path)
        except RustParseError as error:
            raise RustParseError(f"{type_path}: {error}") from error
        if rust_type is not None:
            if rust_type.name in types:
                raise RustParseError(f"{type_path}: duplicate type {rust_type.name}")
            types[rust_type.name] = rust_type

    errors: dict[str, tuple[RustField, ...]] = {}
    error_dir = path / "src/types/error"
    if error_dir.is_dir():
        for error_path in sorted(error_dir.glob("*.rs")):
            if error_path.name == "builders.rs":
                continue
            error_name = parse_struct_name(error_path)
            if error_name is None:
                raise RustParseError(f"{error_path}: expected a modeled error struct")
            errors[error_name] = parse_fields(error_path)
    error_index = path / "src/types/error.rs"
    try:
        event_errors = parse_error_variants(error_index.read_text()) if error_index.exists() else {}
    except RustParseError as error:
        raise RustParseError(f"{error_index}: {error}") from error
    return RustCrate(
        path=path,
        operations=operations,
        types=types,
        errors=errors,
        event_errors=event_errors,
    )
