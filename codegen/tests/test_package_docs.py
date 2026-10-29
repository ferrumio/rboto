"""Keep published package documentation aligned with the public Python contract."""

import ast
import html
import re
import tomllib
from pathlib import Path

import pytest
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import list_services

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("service", list_services(), ids=lambda service: service.service_id)
def test_package_readme_documents_the_complete_public_api(service: ServiceDescriptor) -> None:
    package = ROOT / "packages" / service.distribution_name
    project = tomllib.loads((package / "pyproject.toml").read_text())["project"]
    assert service.display_name
    assert "rboto:" in project["description"]
    assert service.display_name in project["description"]
    assert "Alpha preview." in project["description"]
    assert project["readme"] == "README.md"
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert (package / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert {
        "Development Status :: 3 - Alpha",
        "Framework :: AsyncIO",
        "Typing :: Typed",
    } <= set(project["classifiers"])
    assert (ROOT / "docs" / (service.documentation_path.rstrip("/") + ".md")).is_file()

    readme = (package / "README.md").read_text()
    assert "**Preview (Alpha).**" in readme
    example = ROOT / "examples/clients" / f"{service.python_name}.py"
    assert f"```python\n{example.read_text().rstrip()}\n```" in readme

    tree = ast.parse((package / "python" / service.python_package / "client.py").read_text())
    client = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == service.client_class
    )
    expected = {
        method.name: method for method in client.body if isinstance(method, ast.AsyncFunctionDef)
    }
    table = re.findall(r"^\| \[`(\w+)`\]\(#(\w+)\) \| (.*?) \| (.*?) \|$", readme, re.M)
    assert len(table) == len(expected)
    assert {name for name, _, _, _ in table} == expected.keys()
    for name, anchor, required, output in table:
        assert name == anchor
        method = expected[name]
        assert method.returns is not None
        assert html.unescape(output.removeprefix("<code>").removesuffix("</code>")) == (
            ast.unparse(method.returns)
        )
        names = [
            html.unescape(declaration).split(":")[0]
            for declaration in re.findall(r"<code>(.*?)</code>", required)
        ]
        assert names == [
            arg.arg for arg, default in zip(
                method.args.kwonlyargs, method.args.kw_defaults, strict=True,
            ) if default is None
        ]

    documented = re.findall(
        r"^### (\w+)\n\n```python\n(.*?)\n```", readme, re.M | re.S,
    )
    assert {name for name, _ in documented} == expected.keys()
    assert len(documented) == len(expected)
    for name, source in documented:
        signature = ast.parse(source).body[0]
        assert isinstance(signature, ast.AsyncFunctionDef)
        actual = expected[name]
        assert signature.name == actual.name
        assert not signature.args.args
        assert [ast.dump(arg) for arg in signature.args.kwonlyargs] == [
            ast.dump(arg) for arg in actual.args.kwonlyargs
        ]
        assert [
            ast.dump(default) if default is not None else None
            for default in signature.args.kw_defaults
        ] == [
            ast.dump(default) if default is not None else None
            for default in actual.args.kw_defaults
        ]
        assert signature.returns is not None and actual.returns is not None
        assert ast.dump(signature.returns) == ast.dump(actual.returns)


@pytest.mark.parametrize("directory", ["packages/rboto", "codegen"])
def test_shared_packages_include_preview_readme_and_license(directory: str) -> None:
    package = ROOT / directory
    project = tomllib.loads((package / "pyproject.toml").read_text())["project"]
    assert "rboto:" in project["description"] and "Alpha preview." in project["description"]
    assert "**Preview (Alpha).**" in (package / project["readme"]).read_text()
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert (package / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
