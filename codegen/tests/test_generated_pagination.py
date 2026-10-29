import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.model import Pagination, ServiceDescriptor
from rboto_codegen.pagination import resolve_pagination
from rboto_codegen.registry import get_service, list_services, load_service, service_config_dir
from rboto_codegen.smithy import SmithyService, load_model

ROOT = Path(__file__).parents[2]


@pytest.fixture
def modeled(tmp_path: Path) -> tuple[ServiceDescriptor, SmithyService]:
    model: dict[str, object] = {
        "smithy": "2.0",
        "shapes": {
            "test#Service": {
                "type": "service", "version": "1",
                "traits": {"smithy.api#paginated": {
                    "inputToken": "From", "outputToken": "Next", "pageSize": "Limit",
                }},
                "operations": [{"target": "test#List"}, {"target": "test#Get"}],
            },
            "test#List": {
                "type": "operation", "input": {"target": "test#Input"},
                "output": {"target": "test#Output"},
                "traits": {"smithy.api#paginated": {"outputToken": "Nested.Next"}},
            },
            "test#Get": {
                "type": "operation", "input": {"target": "test#Input"},
                "output": {"target": "test#Output"},
            },
            "test#Input": {
                "type": "structure", "members": {
                    "From": {"target": "smithy.api#String"},
                    "Limit": {"target": "smithy.api#Integer"},
                    "Stream": {"target": "test#Stream"},
                },
            },
            "test#Output": {
                "type": "structure", "members": {
                    "Nested": {"target": "test#Cursor"}, "Items": {"target": "test#Items"},
                    "Next": {"target": "smithy.api#String"},
                    "More": {"target": "smithy.api#Boolean"},
                    "Stream": {"target": "test#Stream"},
                },
            },
            "test#Cursor": {
                "type": "structure", "members": {"Next": {"target": "smithy.api#String"}},
            },
            "test#Items": {"type": "list", "member": {"target": "test#Cursor"}},
            "test#Stream": {"type": "blob", "traits": {"smithy.api#streaming": {}}},
        },
    }
    raw = json.dumps(model).encode()
    path = tmp_path / "model.json"
    path.write_bytes(raw)
    descriptor = replace(get_service("sns"), model_sha256=hashlib.sha256(raw).hexdigest())
    return descriptor, load_model(path, descriptor)


def test_operation_traits_inherit_defaults_and_resolve_python_names(
    modeled: tuple[ServiceDescriptor, SmithyService],
) -> None:
    descriptor, service = modeled
    specs = resolve_pagination(
        descriptor, service, {"list"}, lambda shape, member: member.rust_name,
    )
    assert len(specs) == 1
    assert specs[0].operation == "list"
    assert specs[0].input_tokens == ("from_",)
    assert specs[0].output_tokens == ((("nested", "next"),),)
    assert specs[0].page_size == "limit"


def test_supplement_supports_fallback_list_index_and_completion(
    modeled: tuple[ServiceDescriptor, SmithyService],
) -> None:
    descriptor, service = modeled
    descriptor = replace(descriptor, pagination=(("Get", Pagination(
        ("From",), ("Next || Items[-1].Next",), "Limit", "More",
    )),))
    specs = resolve_pagination(descriptor, service, {"list", "get"}, lambda s, m: m.rust_name)
    spec = next(item for item in specs if item.operation == "get")
    assert spec.output_tokens == ((("next",), ("items", -1, "next")),)
    assert spec.more_results == ("more",)


@pytest.mark.parametrize(
    ("operation", "config", "error"),
    [
        ("Missing", Pagination(("From",), ("Next",)), "unknown operations"),
        ("Get", Pagination(("Missing",), ("Next",)), "unknown pagination input"),
        ("Get", Pagination(("From",), ("Missing",)), "unknown pagination output"),
        ("Get", Pagination(("From",), ("Nested[0]",)), "requires a list"),
        ("Get", Pagination(("From",), ("Next.Value",)), "crosses a scalar"),
        ("Get", Pagination(("From",), ("Items[*].Next",)), "unsupported pagination path"),
        ("Get", Pagination(("Limit",), ("Next",)), "incompatible pagination tokens"),
        ("Get", Pagination(("From",), ("Next",), "From"), "page size must be an integer"),
        ("Get", Pagination(("From",), ("Next",), more_results="Next"), "must be boolean"),
        ("Get", Pagination(("Stream",), ("Stream",)), "cannot own streams"),
        ("Get", Pagination(("From",), ()), "token counts differ"),
    ],
)
def test_invalid_metadata_fails_generation(
    modeled: tuple[ServiceDescriptor, SmithyService],
    operation: str,
    config: Pagination,
    error: str,
) -> None:
    descriptor, service = modeled
    descriptor = replace(descriptor, pagination=((operation, config),))
    with pytest.raises(ValueError, match=error):
        resolve_pagination(descriptor, service, {"list", "get"}, lambda s, m: m.rust_name)


@pytest.mark.parametrize("entry", [
    'input_tokens = []\noutput_tokens = ["Next"]',
    'input_tokens = ["From"]\noutput_tokens = []',
    'input_tokens = ["From", "From"]\noutput_tokens = ["A", "B"]',
    'input_tokens = [1]\noutput_tokens = ["Next"]',
    'input_tokens = ["From"]\noutput_tokens = ["Next"]\nunknown = true',
])
def test_invalid_descriptor_tables_are_rejected(tmp_path: Path, entry: str) -> None:
    path = tmp_path / "sns.toml"
    path.write_text(
        (service_config_dir() / "sns.toml").read_text() + "\n[pagination.List]\n" + entry,
    )
    with pytest.raises(ValueError, match="pagination"):
        load_service(path)


@pytest.mark.parametrize("descriptor", list_services(), ids=lambda item: item.service_id)
def test_generated_paginators_match_model_and_strict_operation_signatures(
    descriptor: ServiceDescriptor,
) -> None:
    generator = ServiceGenerator(
        descriptor, ROOT / "codegen/models" / f"{descriptor.service_id}.json", ROOT,
    )
    package = (
        ROOT / "packages" / descriptor.distribution_name / "python" / descriptor.python_package
    )
    assert not (package / "inputs.py").exists()
    client = next(
        node for node in ast.parse((package / "client.py").read_text()).body
        if isinstance(node, ast.ClassDef)
    )
    operations = {node.name: node for node in client.body if isinstance(node, ast.AsyncFunctionDef)}
    path = package / "paginators.py"
    assert path.exists() == bool(generator.pagination)
    if not generator.pagination:
        assert not any(isinstance(node, ast.FunctionDef) and node.name == "paginators"
                       for node in client.body)
        return
    paginator = next(
        node for node in ast.parse(path.read_text()).body if isinstance(node, ast.ClassDef)
    )
    methods = {
        node.name: node for node in paginator.body
        if isinstance(node, ast.FunctionDef) and node.name != "__init__"
    }
    assert methods.keys() == {item.operation for item in generator.pagination}
    for name, method in methods.items():
        assert ast.dump(method.args) == ast.dump(operations[name].args)
        assert method.args.kwarg is None
        output = operations[name].returns
        assert output is not None and method.returns is not None
        assert ast.unparse(method.returns) == f"AsyncIterator[{ast.unparse(output)}]"
        assert f"client.paginators.{name}(" in (package.parents[1] / "README.md").read_text()
