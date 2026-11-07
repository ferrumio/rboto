from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import ServiceDescriptor


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


@dataclass(frozen=True, slots=True)
class RustCrate:
    path: Path
    operations: dict[str, RustOperation]
    types: dict[str, RustType]
    errors: dict[str, tuple[RustField, ...]] = field(
        default_factory=lambda: dict[str, tuple[RustField, ...]](),
    )


def parse_error_variants(content: str) -> dict[str, tuple[tuple[str, str], ...]]:
    """Read modeled variants only; unhandled SDK errors never expose raw data."""
    return {
        name: tuple(re.findall(
            r"^\s*(\w+)\(\s*crate::types::error::(\w+)\s*\),", body, re.MULTILINE,
        ))
        for name, body in re.findall(r"pub enum (\w+)\s*\{(.*?)\n\}", content, re.DOTALL)
    }


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


def _join_field_lines(body: str) -> tuple[str, ...]:
    result: list[str] = []
    current = ""
    depth = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not current and not line.startswith("pub "):
            continue
        if not current:
            current = line
            depth = line.count("<") - line.count(">")
        else:
            current += " " + line
            depth += line.count("<") - line.count(">")
        if depth <= 0 and not current.endswith(":"):
            result.append(current)
            current = ""
            depth = 0
    if current:
        result.append(current)
    return tuple(result)


def parse_fields(path: Path) -> tuple[RustField, ...]:
    if not path.exists():
        return ()
    content = path.read_text()
    match = re.search(r"pub struct \w+\s*\{(.*?)\n\}", content, re.DOTALL)
    if match is None:
        return ()
    fields: list[RustField] = []
    for line in _join_field_lines(match.group(1)):
        field_match = re.match(r"pub\s+((?:r#)?\w+)\s*:\s*(.+?),?$", line)
        if field_match is None:
            continue
        rust_name = field_match.group(1)
        name = rust_name.removeprefix("r#")
        rust_type = field_match.group(2).rstrip(",").strip()
        option = re.fullmatch(r"::std::option::Option<(.+)>", rust_type)
        fields.append(
            RustField(
                name=name,
                rust_name=rust_name,
                rust_type=(
                    option.group(1).strip().rstrip(",").strip()
                    if option is not None
                    else rust_type.strip().rstrip(",").strip()
                ),
                optional=option is not None,
            )
        )
    return tuple(fields)


def parse_struct_name(path: Path) -> str | None:
    if not path.exists():
        return None
    match = re.search(r"pub struct (\w+)\s*\{", path.read_text())
    return match.group(1) if match is not None else None


def parse_builder_required_fields(content: str) -> tuple[str, ...]:
    """Read builder failures, not non-optional fields that may have defaults."""
    build = re.search(r"pub fn build\s*\(\s*self,?\s*\)", content)
    if build is None:
        return ()
    return tuple(
        name.removeprefix("r#")
        for name in re.findall(
            r'BuildError::missing_field\(\s*"((?:r#)?\w+)"', content[build.end():],
        )
    )


def parse_crate(descriptor: ServiceDescriptor) -> RustCrate:
    path = find_crate(descriptor)
    operations: dict[str, RustOperation] = {}
    operation_dir = path / "src" / "operation"
    for candidate in sorted(operation_dir.iterdir()):
        if not candidate.is_dir():
            continue
        name = candidate.name
        input_fields = parse_fields(candidate / f"_{name}_input.rs")
        output_path = candidate / f"_{name}_output.rs"
        output_fields = parse_fields(output_path)
        if output_path.exists():
            operations[name] = RustOperation(
                name,
                input_fields,
                output_fields,
                parse_struct_name(output_path),
            )

    types: dict[str, RustType] = {}
    for path_entry in sorted((path / "src" / "types").iterdir()):
        if path_entry.suffix != ".rs" or path_entry.name == "builders.rs":
            continue
        content = path_entry.read_text()
        struct_match = re.search(r"pub struct (\w+)\s*\{", content)
        if struct_match is not None:
            name = struct_match.group(1)
            types[name] = RustType(
                name,
                parse_fields(path_entry),
                (),
                False,
                bool(
                    re.search(
                        r"pub fn build(?:\s*)\((?:\s*)self,?(?:\s*)\)(?:\s*)->(?:\s*).*Result<",
                        content,
                        re.DOTALL,
                    )
                ),
                True,
                builder_required_fields=parse_builder_required_fields(content),
            )
            continue
        enum_match = re.search(r"pub enum (\w+)\s*\{(.*?)\n\}", content, re.DOTALL)
        if enum_match is None:
            continue
        variants: list[str] = []
        variant_types: list[tuple[str, str]] = []
        union = False
        for line in enum_match.group(2).splitlines():
            variant = re.match(r"\s*(\w+)(?:\((.+)\))?\s*,?", line)
            if variant is None or line.lstrip().startswith(("#", "/")):
                continue
            variants.append(variant.group(1))
            if variant.group(2) is not None and variant.group(1) != "Unknown":
                union = True
                variant_types.append((variant.group(1), variant.group(2)))
        name = enum_match.group(1)
        types[name] = RustType(name, (), tuple(variants), union, False, False, tuple(variant_types))

    errors: dict[str, tuple[RustField, ...]] = {}
    error_dir = path / "src" / "types" / "error"
    if error_dir.is_dir():
        for error_path in sorted(error_dir.glob("*.rs")):
            if (error_name := parse_struct_name(error_path)) is not None:
                errors[error_name] = parse_fields(error_path)
    return RustCrate(path=path, operations=operations, types=types, errors=errors)
