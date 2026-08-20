from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class RustCrate:
    path: Path
    operations: dict[str, RustOperation]
    types: dict[str, RustType]


def find_crate(descriptor: ServiceDescriptor) -> Path:
    registry = Path.home() / ".cargo" / "registry" / "src"
    expected = f"{descriptor.rust_crate}-{descriptor.rust_crate_version}"
    for index in registry.iterdir():
        candidate = index / expected
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"{expected} is not available in the Cargo registry; run cargo fetch"
    )


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
        if input_fields or output_fields:
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
            )
            continue
        enum_match = re.search(r"pub enum (\w+)\s*\{(.*?)\n\}", content, re.DOTALL)
        if enum_match is None:
            continue
        variants: list[str] = []
        union = False
        for line in enum_match.group(2).splitlines():
            variant = re.match(r"\s*(\w+)(?:\((.+)\))?\s*,?", line)
            if variant is None or line.lstrip().startswith(("#", "/")):
                continue
            variants.append(variant.group(1))
            if variant.group(2) is not None and variant.group(1) != "Unknown":
                union = True
        name = enum_match.group(1)
        types[name] = RustType(name, (), tuple(variants), union, False, False)

    return RustCrate(path=path, operations=operations, types=types)
