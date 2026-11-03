"""References must describe actual public contracts, including recursive and stream types."""

import ast
import re
from functools import cache
from pathlib import Path

import pytest
from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.reference import (
    document_types,
    model_excerpt,
    reachable,
    types_from_contract,
    write_reference,
)
from rboto_codegen.reference_site import check_links
from rboto_codegen.registry import list_services

ROOT = Path(__file__).parents[2]


@cache
def reference_for(service_id: str) -> dict[str, str]:
    descriptor = next(item for item in list_services() if item.service_id == service_id)
    generator = ServiceGenerator(
        descriptor, ROOT / "codegen/models" / f"{service_id}.json", ROOT,
    )
    return generator.render_reference()


@pytest.mark.parametrize("descriptor", list_services(), ids=lambda item: item.service_id)
def test_every_reference_matches_the_current_public_contract(
    descriptor: ServiceDescriptor, tmp_path: Path,
) -> None:
    package = (ROOT / "packages" / descriptor.distribution_name / "python"
               / descriptor.python_package)
    reference = tmp_path / descriptor.service_id
    # Compare the generated bindings against pages freshly rendered from the models.
    client_source = (package / "client.py").read_text()
    types_source = (package / "types.py").read_text()
    stub_source = (package / "_native.pyi").read_text()
    assert not (package / "_reference").exists()
    pages = reference_for(descriptor.service_id)
    assert all(path.endswith(".md") for path in pages)
    write_reference(reference, pages)
    check_links(reference)
    assert len({path.casefold() for path in pages}) == len(pages)
    sources = [client_source, types_source, stub_source]
    if (package / "paginators.py").exists():
        sources.append((package / "paginators.py").read_text())
    for source in sources:
        ast.parse(source)
        for url in re.findall(r'https://ferrumio.github.io/rboto/reference/[^"\s)]+', source):
            path, _, anchor = url.partition("#")
            relative = path.split(f"/{descriptor.service_id}/", 1)[1].rstrip("/")
            filename = f"{relative}.md" if relative else "index.md"
            assert filename in pages, url
            if anchor:
                assert anchor == "pagination-and-streaming"
                assert "## Pagination and streaming" in pages[filename]

    # Independently compare published signatures with the Python methods.
    client = next(node for node in ast.parse(client_source).body
                  if isinstance(node, ast.ClassDef) and node.name == descriptor.client_class)
    methods = {node.name: node for node in client.body if isinstance(node, ast.AsyncFunctionDef)}
    assert {p.stem for p in (reference / "operations").glob("*.md")} == methods.keys()
    exports = types_from_contract(types_source, stub_source)
    for name, method in methods.items():
        text = (reference / "operations" / f"{name}.md").read_text()
        blocks = re.findall(r"```python\n(.*?)\n```", text, re.S)
        signature = ast.parse(blocks[1]).body[0]
        assert isinstance(signature, ast.AsyncFunctionDef)
        assert signature.returns is not None and method.returns is not None
        assert ast.dump(signature.returns) == ast.dump(method.returns)
        assert [ast.dump(arg) for arg in signature.args.kwonlyargs] == [
            ast.dump(arg) for arg in method.args.kwonlyargs
        ]
        assert [ast.dump(arg) if arg is not None else None
                for arg in signature.args.kw_defaults] == [
            ast.dump(arg) if arg is not None else None for arg in method.args.kw_defaults
        ]
        assert not signature.args.args and signature.args.kwarg is None
        for statement in ast.parse(blocks[0]).body:
            assert isinstance(statement, ast.ImportFrom)
            if statement.module == f"{descriptor.python_package}.types":
                assert {alias.name for alias in statement.names} <= exports.keys()
        assert "_native" not in text and "_types." not in text


def test_recursive_shapes_and_native_alias_collisions() -> None:
    contract = types_from_contract(
        """
from typing import TypeAlias, TypedDict, NotRequired, Required
NodeOutput: TypeAlias = _native.Node
class Node(TypedDict):
    child: NotRequired[Node]
class EventUnknown(TypedDict):
    unknown: Required[Literal[True]]
Event: TypeAlias = Node | EventUnknown
type JSONValue = None | str | list[JSONValue]
""",
        """
class Node:
    @property
    def child(self) -> Node | None: ...
    @property
    def event(self) -> _types.Event: ...
    def to_dict(self) -> dict[str, object]: ...
""",
    )
    assert contract["Node"].fields[0].required is False
    assert contract["EventUnknown"].fields[0].required is True
    assert contract["NodeOutput"].fields[0].annotation == "NodeOutput | None"
    assert contract["NodeOutput"].fields[1].annotation == "Event"
    assert contract["NodeOutput"].kind == "native"
    assert reachable({"Node"}, contract) == {"Node"}
    assert reachable({"NodeOutput"}, contract) == {"NodeOutput", "Event", "Node", "EventUnknown"}
    assert reachable({"JSONValue"}, contract) == {"JSONValue"}


def test_type_paths_are_distinct_on_case_insensitive_filesystems() -> None:
    source = """
CSV: TypeAlias = str
Csv: TypeAlias = str
Index: TypeAlias = str
"""
    types = types_from_contract(source, "")
    filenames = {item.filename.casefold() for item in types.values()}
    assert len(filenames) == 3
    assert "index.md" not in filenames
    reverse = types_from_contract("\n".join(reversed(source.strip().splitlines())), "")
    assert {name: item.filename for name, item in types.items()} == {
        name: item.filename for name, item in reverse.items()
    }


def test_real_output_dictionaries_streams_and_unknown_variants() -> None:
    package = ROOT / "packages/rboto-bedrock-runtime/python/rboto_bedrock_runtime"
    reference = reference_for("bedrock-runtime")
    operation = reference["operations/converse_stream.md"]
    assert "Typed response mapping" in operation
    assert "ConverseStreamEventOutput" in operation
    assert "Output streams:" in operation
    assert "No output stream" not in operation
    assert "ModelStreamError" in operation
    types = types_from_contract((package / "types.py").read_text(),
                                (package / "_native.pyi").read_text())
    assert types["ConverseStreamOutput"].kind == "mapping"
    assert types["ConverseStreamEventOutputUnknown"].fields[0].annotation == "Literal[True]"
    assert "ConverseStreamEventOutputUnknown" not in reachable({"Message"}, types)
    bidirectional = reference["operations/invoke_model_with_bidirectional_stream.md"]
    assert "Input event streams" in bidirectional
    assert "Request byte bodies" not in bidirectional


def test_real_native_fields_use_public_names_and_keep_nullability() -> None:
    reference = reference_for("s3")
    text = reference["types/ListBucketsOutput.md"]
    assert "list[BucketOutput] | None" in text
    assert "[`BucketOutput`](BucketOutput.md)" in text
    stream = reference["operations/get_object.md"]
    assert "Read-only native attributes" in stream
    assert "`ByteStream.read()` buffers the complete body" in stream
    assert "../example.md" in stream
    paginated = reference["operations/list_objects_v2.md"]
    assert "client.paginators.list_objects_v2" in paginated
    assert "continuation_token" in paginated


def test_reference_link_validation_detects_missing_files_and_anchors(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("[Missing](types/Missing.md)")
    with pytest.raises(ValueError, match="Broken reference link"):
        check_links(tmp_path)
    (tmp_path / "index.md").write_text("[Missing](#missing)")
    with pytest.raises(ValueError, match="Broken reference anchor"):
        check_links(tmp_path)
    (tmp_path / "index.md").write_text("# Existing\n\n[Works](#existing)")
    check_links(tmp_path)


def test_type_docstrings_preserve_contracts_and_native_aliases() -> None:
    source = '''
from typing import TypeAlias, TypedDict, NotRequired
Node = TypedDict("Node", {"child": NotRequired["Node"]})
NodeOutput: TypeAlias = _native.Node
Choice: TypeAlias = Literal["one", "two"]
type JSONValue = None | str | list[JSONValue]
'''
    stub = '''
class Node:
    @property
    def child(self) -> Node | None: ...
    def to_dict(self) -> dict[str, object]: ...
'''
    documented_source, documented_stub = document_types(source, stub, "test")
    assert types_from_contract(documented_source, documented_stub) == types_from_contract(
        source, stub,
    )
    native = ast.parse(documented_stub).body[0]
    assert isinstance(native, ast.ClassDef)
    assert ast.get_docstring(native) == (
        "See [reference](https://ferrumio.github.io/rboto/reference/test/types/NodeOutput/)."
    )
    for name in ("Node", "NodeOutput", "Choice", "JSONValue"):
        assert f"/test/types/{name}/" in documented_source
    assert ".__doc__ =" not in documented_source


def test_generation_prunes_obsolete_pages_and_is_deterministic(tmp_path: Path) -> None:
    write_reference(tmp_path, {"operations/old.md": "old"})
    files = {"operations/new.md": "new", "types/Tree.md": "recursive"}
    write_reference(tmp_path, files)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*.md")}
    write_reference(tmp_path, dict(reversed(list(files.items()))))
    assert before == {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*.md")}
    assert not (tmp_path / "operations/old.md").exists()


def test_model_excerpts_cannot_inject_markdown_links_or_html() -> None:
    text = model_excerpt('<p>A [link](missing.md) | <script>alert("x")</script></p>')
    assert "<script>" not in text and "[link]" not in text and " | " not in text
    assert model_excerpt("a " * 500).endswith("...")
