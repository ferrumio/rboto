"""Parse the generated SDK's declaration subset, rejecting unfamiliar syntax.

This is deliberately not a Rust parser. Comments, attributes and balanced
delimiters are handled independently of formatting; unsupported declarations
must fail rather than become empty shapes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


class RustParseError(ValueError):
    pass


_TOKEN = re.compile(
    r"(?P<skip>\s+|//[^\n]*)|(?P<block>/\*)|"
    r'(?P<raw>(?:br|r)(?P<hashes>\#*)".*?"(?P=hashes))|'
    r'"(?:\\.|[^"\\])*"|'
    r"'(?:\\.|[^'\\])'|"
    r"(?:r#)?[A-Za-z_]\w*|::|->|=>|.",
    re.DOTALL,
)
_IDENTIFIER = re.compile(r"(?:r#)?[A-Za-z_]\w*\Z")


def tokens(source: str) -> tuple[str, ...]:
    result: list[str] = []
    position = 0
    while position < len(source):
        match = _TOKEN.match(source, position)
        if match is None:
            raise RustParseError(f"unrecognized Rust syntax at offset {position}")
        position = match.end()
        if match.lastgroup == "skip":
            continue
        if match.lastgroup == "block":
            depth = 1
            for marker in re.finditer(r"/\*|\*/", source[position:]):
                depth += 1 if marker.group() == "/*" else -1
                if not depth:
                    position += marker.end()
                    break
            else:
                raise RustParseError("unterminated Rust block comment")
            continue
        result.append(match.group())
    return tuple(result)


def closing(parts: tuple[str, ...], start: int, *, generics: bool = False) -> int:
    pairs = {"(": ")", "{": "}", "[": "]"}
    if generics:
        pairs["<"] = ">"
    stack = [pairs[parts[start]]]
    for index in range(start + 1, len(parts)):
        token = parts[index]
        if token in pairs:
            stack.append(pairs[token])
        elif token in pairs.values():
            if token != stack.pop():
                raise RustParseError(
                    f"unbalanced delimiter near {' '.join(parts[start : index + 1])}"
                )
            if not stack:
                return index
    raise RustParseError(f"unterminated declaration near {' '.join(parts[start : start + 12])}")


def split_items(parts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    items: list[tuple[str, ...]] = []
    start = index = 0
    while index < len(parts):
        if parts[index] in {"(", "{", "[", "<"}:
            index = closing(parts, index, generics=True)
        elif parts[index] == ",":
            if index == start:
                raise RustParseError("empty declaration between commas")
            items.append(parts[start:index])
            start = index + 1
        elif parts[index] in {")", "}", "]", ">"}:
            raise RustParseError(f"unexpected delimiter {parts[index]}")
        index += 1
    if start < len(parts):
        items.append(parts[start:])
    return tuple(items)


def without_attributes(parts: tuple[str, ...]) -> tuple[str, ...]:
    while parts[:2] == ("#", "["):
        parts = parts[closing(parts, 1) + 1 :]
    return parts


@dataclass(frozen=True, slots=True)
class Declaration:
    name: str
    body: tuple[str, ...]


def declarations(parts: tuple[str, ...], kind: str) -> tuple[Declaration, ...]:
    result: list[Declaration] = []
    for index in range(len(parts) - 2):
        if parts[index : index + 2] != ("pub", kind):
            continue
        name = parts[index + 2]
        if not _IDENTIFIER.fullmatch(name) or parts[index + 3 : index + 4] != ("{",):
            raise RustParseError(f"unsupported public {kind} declaration near {name}")
        end = closing(parts, index + 3)
        result.append(Declaration(name, parts[index + 4 : end]))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class TypeRef:
    name: str
    arguments: tuple[TypeRef, ...] = ()

    def render(self) -> str:
        if not self.arguments:
            return self.name
        return f"{self.name}<{', '.join(item.render() for item in self.arguments)}>"


def parse_type(parts: tuple[str, ...]) -> TypeRef:
    index = 1 if parts[:1] == ("::",) else 0
    if index >= len(parts) or not _IDENTIFIER.fullmatch(parts[index]):
        raise RustParseError(f"unsupported Rust type: {' '.join(parts)}")
    index += 1
    while parts[index : index + 1] == ("::",):
        if index + 1 >= len(parts) or not _IDENTIFIER.fullmatch(parts[index + 1]):
            raise RustParseError(f"unsupported Rust type path: {' '.join(parts)}")
        index += 2
    name = "".join(parts[:index])
    if name.startswith(("std::", "aws_smithy_")):
        name = "::" + name
    arguments: tuple[TypeRef, ...] = ()
    if index != len(parts):
        if parts[index] != "<" or closing(parts, index, generics=True) != len(parts) - 1:
            raise RustParseError(f"unsupported Rust type: {' '.join(parts)}")
        arguments = tuple(parse_type(item) for item in split_items(parts[index + 1 : -1]))
        if not arguments:
            raise RustParseError(f"empty generic arguments: {name}")
    return TypeRef(name, arguments)


def enum_variants(declaration: Declaration) -> tuple[tuple[str, TypeRef | None], ...]:
    variants: list[tuple[str, TypeRef | None]] = []
    for item in split_items(declaration.body):
        item = without_attributes(item)
        name = item[0] if item else ""
        if not _IDENTIFIER.fullmatch(name):
            raise RustParseError(f"{declaration.name}: unsupported enum variant {' '.join(item)}")
        payload = None
        if len(item) > 1:
            if item[1] != "(" or closing(item, 1) != len(item) - 1:
                raise RustParseError(f"{declaration.name}.{name}: unsupported enum variant")
            arguments = split_items(item[2:-1])
            if len(arguments) != 1:
                raise RustParseError(f"{declaration.name}.{name}: expected one variant payload")
            payload = parse_type(arguments[0])
        if name in {variant for variant, _ in variants}:
            raise RustParseError(f"{declaration.name}: duplicate variant {name}")
        variants.append((name, payload))
    return tuple(variants)


def enum_values(parts: tuple[str, ...], declaration: Declaration) -> tuple[str, ...]:
    methods = [
        index
        for index in range(len(parts) - 2)
        if parts[index : index + 3] == ("pub", "fn", "as_str")
    ]
    if len(methods) != 1:
        raise RustParseError(f"{declaration.name}: missing or ambiguous enum as_str method")
    try:
        start = parts.index("{", methods[0] + 3)
    except ValueError as error:
        raise RustParseError(f"{declaration.name}: missing enum as_str body") from error
    body = parts[start + 1 : closing(parts, start)]
    known = {name for name, _ in enum_variants(declaration)} - {"Unknown"}
    values: dict[str, str] = {}
    for index in range(len(body) - 4):
        if (
            body[index] in {declaration.name, "Self"}
            and body[index + 1] == "::"
            and body[index + 3] == "=>"
        ):
            variant = body[index + 2]
            if variant not in known:
                raise RustParseError(f"{declaration.name}.{variant}: unmodeled enum string arm")
            try:
                value = json.loads(body[index + 4])
            except ValueError as error:
                raise RustParseError(
                    f"{declaration.name}.{variant}: expected a literal enum string"
                ) from error
            if not isinstance(value, str) or variant in values:
                raise RustParseError(f"{declaration.name}.{variant}: invalid enum string arm")
            values[variant] = value
    if values.keys() != known or len(set(values.values())) != len(known):
        raise RustParseError(f"{declaration.name}: incomplete or duplicate enum string mapping")
    return tuple(values.values())


def builder(
    parts: tuple[str, ...],
    *,
    expected_type: str | None = None,
) -> tuple[bool, tuple[str, ...]] | None:
    builds = [
        index
        for index in range(len(parts) - 2)
        if parts[index : index + 3] == ("pub", "fn", "build")
    ]
    if not builds:
        return None
    if len(builds) != 1:
        raise RustParseError("expected exactly one public builder build method")
    index = builds[0] + 3
    if parts[index : index + 1] != ("(",):
        raise RustParseError("unsupported builder build signature")
    end = closing(parts, index)
    if parts[index + 1 : end] not in {("self",), ("self", ",")}:
        raise RustParseError("builder build must consume self")
    start = end + 1
    try:
        body_start = parts.index("{", start)
    except ValueError as error:
        raise RustParseError("missing builder build body") from error
    if parts[start : start + 1] != ("->",):
        raise RustParseError("missing builder return type")
    return_type = parse_type(parts[start + 1 : body_start])
    fallible = return_type.name in {
        "Result",
        "::std::result::Result",
        "::core::result::Result",
        "core::result::Result",
    }
    if fallible and len(return_type.arguments) != 2:
        raise RustParseError("builder Result must contain success and error types")
    result_type = return_type.arguments[0] if fallible else return_type
    if expected_type is not None and result_type.name.rsplit("::", 1)[-1] != expected_type:
        raise RustParseError(f"builder must return {expected_type}, got {result_type.render()}")
    body = parts[body_start + 1 : closing(parts, body_start)]
    required: list[str] = []
    for index in range(len(body) - 2):
        if body[index : index + 3] != ("BuildError", "::", "missing_field"):
            continue
        argument = body[index + 4 : index + 5]
        if body[index + 3 : index + 4] != ("(",) or not argument:
            raise RustParseError("unsupported builder missing_field call")
        try:
            name = json.loads(argument[0])
        except (ValueError, TypeError) as error:
            raise RustParseError("builder missing_field requires a literal field name") from error
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise RustParseError("builder missing_field requires a literal field name")
        required.append(name.removeprefix("r#"))
    if required and not fallible:
        raise RustParseError("infallible builder contains missing-field failures")
    return fallible, tuple(dict.fromkeys(required))
