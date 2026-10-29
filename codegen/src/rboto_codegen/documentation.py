"""Render package documentation from the same in-memory API emitted as Python."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from html import escape
from pathlib import Path

from .manifests import template_environment
from .model import ServiceDescriptor


@dataclass(frozen=True, slots=True)
class DocumentedOperation:
    name: str
    required: str
    output: str
    signature: str


def _code(text: str) -> str:
    return f"<code>{escape(text).replace('|', '&#124;')}</code>"


def operations_from_client(
    client_source: str, client_class: str,
) -> tuple[DocumentedOperation, ...]:
    """Read the just-rendered API, never a previously generated file on disk."""
    client = next(
        node for node in ast.parse(client_source).body
        if isinstance(node, ast.ClassDef) and node.name == client_class
    )
    operations: list[DocumentedOperation] = []
    for method in client.body:
        if not isinstance(method, ast.AsyncFunctionDef):
            continue
        required: list[str] = []
        parameters: list[str] = []
        for argument, default in zip(method.args.kwonlyargs, method.args.kw_defaults, strict=True):
            assert argument.annotation is not None
            declaration = f"{argument.arg}: {ast.unparse(argument.annotation)}"
            if default is None:
                required.append(_code(declaration))
            else:
                declaration += f" = {ast.unparse(default)}"
            parameters.append(declaration)
        assert method.returns is not None
        output = ast.unparse(method.returns)
        signature = (
            f"async def {method.name}(\n    *,\n"
            + "".join(f"    {parameter},\n" for parameter in parameters)
            + f") -> {output}: ..."
            if parameters else f"async def {method.name}() -> {output}: ..."
        )
        operations.append(DocumentedOperation(
            name=method.name,
            required=", ".join(required) or "None",
            output=_code(output),
            signature=signature,
        ))
    return tuple(sorted(operations, key=lambda operation: operation.name))


def render_service_readme(
    descriptor: ServiceDescriptor, client_source: str, repository_root: Path,
    paginators: tuple[str, ...] = (),
) -> str:
    example = repository_root / "examples/clients" / f"{descriptor.python_name}.py"
    return template_environment().get_template("service_readme.md.j2").render(
        service=descriptor,
        example=example.read_text().rstrip(),
        operations=operations_from_client(client_source, descriptor.client_class),
        paginators=paginators,
    )
