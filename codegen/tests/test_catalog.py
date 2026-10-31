import ast
import json
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import get_service, list_services
from rboto_codegen.rust_crate import parse_crate
from rboto_codegen.smithy import load_model

ROOT = Path(__file__).parents[2]


class TypeRenderer(ServiceGenerator):
    def render_types(self) -> str:
        self._configure_python_types()
        return self._render_python_types()

    def render_client(self) -> str:
        return self._render_python_client()


def test_cloudwatch_live_tail_errors_have_public_exception_classes() -> None:
    path = ROOT / "packages/rboto-cloudwatch-logs/python/rboto_cloudwatch_logs/exceptions.py"
    classes = {
        node.name: node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.ClassDef)
    }
    for name in ("SessionStreamingError", "SessionTimeoutError", "ResourceNotFoundError"):
        assert name in classes
        base = classes[name].bases[0]
        assert isinstance(base, ast.Name)
        assert base.id == "CloudWatchLogsError"


def test_all_clients_have_installable_extras_and_typecheck_paths() -> None:
    project = tomllib.loads((ROOT / "packages/rboto/pyproject.toml").read_text())["project"]
    extras = project["optional-dependencies"]
    paths = json.loads((ROOT / "pyrightconfig.json").read_text())["extraPaths"]
    expected_all: list[str] = []
    for service in list_services():
        dependency = f"{service.distribution_name}=={project['version']}"
        assert extras[service.service_id] == [dependency]
        expected_all.append(dependency)
        assert f"packages/{service.distribution_name}/python" in paths
    assert sorted(extras["all"]) == sorted(expected_all)


@pytest.mark.parametrize("service", list_services(), ids=lambda service: service.service_id)
def test_generated_signatures_cover_pinned_rust_operations(service: ServiceDescriptor) -> None:
    path = ROOT / f"packages/{service.distribution_name}/python/{service.python_package}"
    client = ast.parse((path / "client.py").read_text())
    methods = {
        node.name: node for node in ast.walk(client) if isinstance(node, ast.AsyncFunctionDef)
    }
    operations = parse_crate(service).operations
    assert methods.keys() == operations.keys()
    for name, method in methods.items():
        assert [arg.arg for arg in method.args.args] == ["self"]
        assert method.args.vararg is None
        assert method.args.kwarg is None
        assert method.returns is not None
        assert len(method.args.kwonlyargs) == len(operations[name].input_fields)


@pytest.mark.parametrize("service", list_services(), ids=lambda service: service.service_id)
def test_every_operation_returns_its_own_output_type(service: ServiceDescriptor) -> None:
    path = ROOT / f"packages/{service.distribution_name}/python/{service.python_package}"
    model = load_model(ROOT / "codegen/models" / f"{service.service_id}.json", service)
    expected = {
        operation.rust_name: (
            "None" if operation.output_target in {None, "smithy.api#Unit"}
            else f"{operation.name}Output"
        )
        for operation in model.operations
    }
    client = ast.parse((path / "client.py").read_text())
    for method in ast.walk(client):
        if isinstance(method, ast.AsyncFunctionDef):
            assert method.returns is not None
            assert ast.unparse(method.returns) == expected[method.name]
    native = ast.parse((path / "_native.pyi").read_text())
    native_client = next(
        node for node in native.body
        if isinstance(node, ast.ClassDef) and node.name == service.native_class
    )
    for method in native_client.body:
        if isinstance(method, ast.FunctionDef) and method.name in expected:
            assert method.returns is not None
            assert ast.unparse(method.returns) == f"Awaitable[{expected[method.name]}]"
    declarations = {
        node.target.id
        for node in ast.parse((path / "types.py").read_text()).body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    declarations.update(
        target.id
        for node in ast.parse((path / "types.py").read_text()).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    )
    assert set(expected.values()) - {"None"} <= declarations


@pytest.mark.parametrize("service", list_services(), ids=lambda service: service.service_id)
def test_nested_typed_dict_keys_match_rust_converters(service: ServiceDescriptor) -> None:
    path = ROOT / f"packages/{service.distribution_name}/python/{service.python_package}/types.py"
    mappings: dict[str, set[str]] = {}
    for node in ast.parse(path.read_text()).body:
        if not (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "TypedDict"
        ):
            continue
        name, fields = node.value.args
        assert isinstance(name, ast.Constant) and isinstance(name.value, str)
        assert isinstance(fields, ast.Dict)
        mappings[name.value.lower()] = {
            key.value
            for key in fields.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    for rust in parse_crate(service).types.values():
        if rust.is_struct and rust.name.lower() in mappings:
            assert mappings[rust.name.lower()] == {field.name for field in rust.fields}, rust.name


@pytest.mark.parametrize("service", list_services(), ids=lambda service: service.service_id)
def test_every_native_response_class_has_a_public_type_alias(service: ServiceDescriptor) -> None:
    path = ROOT / f"packages/{service.distribution_name}/python/{service.python_package}"
    native_classes = {
        node.name for node in ast.parse((path / "_native.pyi").read_text()).body
        if isinstance(node, ast.ClassDef) and node.name != service.native_class
    }
    exported = {
        node.value.attr
        for node in ast.parse((path / "types.py").read_text()).body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "_native"
    }
    assert exported == native_classes


def test_new_model_names_do_not_get_overwritten_by_native_aliases() -> None:
    generator = TypeRenderer(get_service("s3"), ROOT / "codegen/models/s3.json", ROOT)
    bucket = next(shape for shape in generator.service.shapes.values() if shape.name == "Bucket")
    for name in ("BucketOutput", "BucketValueOutput"):
        shape = replace(bucket, shape_id=f"example#{name}", name=name)
        generator.service.shapes[shape.shape_id] = shape
    source = generator.render_types()
    declarations: dict[str, ast.expr] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assert target.id not in declarations
                    declarations[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assert node.value is not None
            assert node.target.id not in declarations
            declarations[node.target.id] = node.value
    for name in ("Bucket", "BucketOutput", "BucketValueOutput"):
        value = declarations[name]
        assert isinstance(value, ast.Call)
        assert isinstance(value.func, ast.Name) and value.func.id == "TypedDict"
    alias = declarations["BucketValueValueOutput"]
    assert isinstance(alias, ast.Attribute) and alias.attr == "Bucket"
    assert generator.render_types() == source


def test_operation_output_name_takes_priority_over_an_input_shape() -> None:
    generator = TypeRenderer(get_service("s3"), ROOT / "codegen/models/s3.json", ROOT)
    shape = next(
        shape for shape in generator.service.shapes.values()
        if shape.name == "CreateBucketConfiguration"
    )
    generator.service.shapes[shape.shape_id] = replace(shape, name="CreateBucketOutput")
    source = generator.render_types()
    assert "CreateBucketInput = TypedDict(" in source
    assert "CreateBucketOutput: TypeAlias = _native.CreateBucketOutput" in source
    method = next(
        node for node in ast.walk(ast.parse(generator.render_client()))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_bucket"
    )
    argument = next(
        arg for arg in method.args.kwonlyargs if arg.arg == "create_bucket_configuration"
    )
    assert argument.annotation is not None
    assert ast.unparse(argument.annotation) == "CreateBucketInput | None"
    assert method.returns is not None
    assert ast.unparse(method.returns) == "CreateBucketOutput"


def test_shared_dictionary_results_have_a_distinct_type_per_operation() -> None:
    generator = TypeRenderer(
        replace(get_service("lambda"), native_outputs=False),
        ROOT / "codegen/models/lambda.json",
        ROOT,
    )
    source = generator.render_types()
    assert "GetLayerVersionResponse = " not in source
    declarations = {
        node.targets[0].id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    names = ("GetLayerVersionOutput", "GetLayerVersionByArnOutput")
    values = [declarations[name] for name in names]
    assert all(isinstance(value, ast.Call) for value in values)
    first, second = values
    assert isinstance(first, ast.Call) and isinstance(second, ast.Call)
    assert ast.dump(first.args[1]) == ast.dump(second.args[1])
