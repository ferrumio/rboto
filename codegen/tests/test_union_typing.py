"""Exercise generated union narrowing as a consumer of the complete catalog."""

import ast
import json
import os
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.reference import Type, types_from_contract, union_example
from rboto_codegen.registry import list_services

ROOT = Path(__file__).parents[2]


@cache
def contracts() -> list[tuple[ServiceDescriptor, dict[str, Type]]]:
    result: list[tuple[ServiceDescriptor, dict[str, Type]]] = []
    for service in list_services():
        package = ROOT / "packages" / service.distribution_name / "python" / service.python_package
        result.append((
            service,
            types_from_contract((package / "types.py").read_text(),
                                (package / "_native.pyi").read_text()),
        ))
    return result


def check(path: Path, checker: str) -> subprocess.CompletedProcess[str]:
    sources = [ROOT / "packages/rboto/src", *sorted(ROOT.glob("packages/rboto-*/python"))]
    if checker == "mypy":
        command = [
            sys.executable, "-m", "mypy", "--strict", "--no-incremental",
            "--follow-imports=silent", str(path),
        ]
    else:
        config = path.parent / "pyrightconfig.json"
        config.write_text(json.dumps({
            "include": [path.name], "extraPaths": [str(source) for source in sources],
            "pythonVersion": "3.12", "typeCheckingMode": "strict",
        }))
        command = [
            sys.executable, "-m", "pyright", "--project", str(config),
            "--pythonpath", sys.executable,
        ]
    return subprocess.run(
        command, cwd=ROOT,
        env={**os.environ, "MYPYPATH": os.pathsep.join(map(str, sources))},
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_every_union_narrows_to_its_exact_variant(tmp_path: Path, checker: str) -> None:
    imports = ["from typing import assert_type"]
    functions: list[str] = []
    for service, types in contracts():
        package = service.python_package
        alias = f"service_{package.removeprefix('rboto_')}"
        used = False
        for name, item in types.items():
            if item.kind != "alias" or not item.references:
                continue
            variants = [types.get(reference) for reference in sorted(item.references)]
            # A modeled union is an alias of the generated one-key dictionaries.
            if any(variant is None or variant.kind != "mapping" or len(variant.fields) != 1
                   for variant in variants):
                continue
            used = True
            function = [f"def check_{alias}_{name}(value: {alias}.{name}) -> None:"]
            for variant in variants:
                assert variant is not None
                assert variant.closed, (package, name, variant.name)
                field = variant.fields[0]
                assert field.required
                function.extend([
                    f"    if {field.name!r} in value:",
                    f"        assert_type(value, {alias}.{variant.name})",
                ])
            functions.append("\n".join(function))
        if used:
            imports.append(f"from {package} import types as {alias}")
    assert functions
    path = tmp_path / "all_unions.py"
    path.write_text("\n".join(imports) + "\n\n" + "\n\n".join(functions) + "\n")
    result = check(path, checker)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_generated_narrowing_examples_typecheck(tmp_path: Path, checker: str) -> None:
    examples: list[str] = []
    for service, types in contracts():
        package = service.python_package
        for item in types.values():
            example = union_example(item, types, package)
            if example:
                # Keep each example's imports and function local to avoid name
                # collisions across unrelated services' public model names.
                examples.append(
                    f"def example_{len(examples)}() -> None:\n"
                    + "\n".join("    " + line if line else "" for line in example.splitlines())
                    + "\n    print(handle)\n"
                )
    assert examples
    path = tmp_path / "reference_examples.py"
    path.write_text("\n\n".join(examples))
    result = check(path, checker)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_union_contract_rejects_unsafe_values_and_structural_extras(
    tmp_path: Path, checker: str,
) -> None:
    path = tmp_path / "invalid_unions.py"
    path.write_text("""
from typing_extensions import TypedDict
from rboto_dynamodb.types import AttributeValue, AttributeValueOutput, AttributeValueS

mixed: AttributeValue = {"s": "text", "n": "1"}  # reject
wrong: AttributeValue = {"s": 1}  # reject
unknown: AttributeValue = {"unknown": True}  # reject
recursive: AttributeValue = {"m": {"child": {"unknown": True}}}  # reject
bad_unknown: AttributeValueOutput = {"unknown": False}  # reject

class Extra(TypedDict):
    s: str
    additional: int

def unsafe(value: AttributeValueOutput, extra: Extra) -> None:
    print(value["s"])  # reject
    selected: AttributeValue = extra  # reject
    print(selected)

class Extended(AttributeValueS):  # closed-extra
    additional: int  # closed-extra
""")
    result = check(path, checker)
    assert result.returncode == 1, result.stdout + result.stderr
    # Every independent invalid value must be diagnosed; an unrelated import
    # error must never turn a negative consumer test into a passing assertion.
    source = path.read_text().splitlines()
    rejected = {index for index, line in enumerate(source, 1) if "# reject" in line}
    for line in rejected:
        assert f":{line}:" in result.stdout, result.stdout
    extra_lines = {index for index, line in enumerate(source, 1) if "# closed-extra" in line}
    assert any(f":{line}:" in result.stdout for line in extra_lines), result.stdout


def test_only_union_variants_are_closed_and_dependency_is_declared() -> None:
    import tomllib

    for service, types in contracts():
        members = set[str]().union(*(
            item.references for item in types.values()
            if item.kind == "alias" and item.references
            and all(reference in types and types[reference].kind == "mapping"
                    for reference in item.references)
        ))
        for name, item in types.items():
            if item.kind == "mapping":
                assert item.closed == (name in members), (service.service_id, name)
        manifest = ROOT / "packages" / service.distribution_name / "pyproject.toml"
        assert "typing-extensions>=4.15,<5" in tomllib.loads(manifest.read_text())[
            "project"
        ]["dependencies"]


def test_runtime_closed_metadata_preserves_recursive_and_unknown_variants() -> None:
    # Execute the generated union declarations themselves, without substituting
    # hand-maintained copies or importing a native extension.
    for service, types in contracts():
        source = (ROOT / "packages" / service.distribution_name / "python"
                  / service.python_package / "types.py")
        tree = ast.parse(source.read_text())
        selected: list[ast.stmt] = [
            node for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in types and types[node.targets[0].id].closed
        ]
        declarations = ast.unparse(ast.Module(body=selected, type_ignores=[]))
        program = (
            "from typing import Required, Literal\nfrom typing_extensions import TypedDict\n"
            + declarations
            + "\n"
            + "\n".join(
                f"assert {name}.__closed__ is True\n"
                f"assert {name}.__required_keys__ == "
                f"frozenset({[field.name for field in item.fields]!r})"
                for name, item in types.items() if item.closed
            )
        )
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
