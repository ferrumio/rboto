import ast
import importlib
import inspect
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
import rboto
from rboto import Session
from rboto_codegen.registry import list_services
from rboto_codegen.rust_crate import parse_crate

SELECTED_SERVICES = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
assert SELECTED_SERVICES <= {service.service_id for service in list_services()}
CLIENTS = tuple(
    (
        getattr(importlib.import_module(service.python_package), service.client_class),
        getattr(importlib.import_module(f"{service.python_package}._native"), service.native_class),
        len(parse_crate(service).operations),
    )
    for service in list_services()
    if not SELECTED_SERVICES or service.service_id in SELECTED_SERVICES
)


class NativeSpy:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __getattr__(self, name: str) -> Callable[[dict[str, object]], Awaitable[object]]:
        async def call(params: dict[str, object]) -> object:
            self.calls.append((name, params))
            return self.result

        return call


def generated_methods(client_class: type[object]) -> dict[str, Callable[..., object]]:
    return {
        name: method
        for name, method in client_class.__dict__.items()
        if inspect.iscoroutinefunction(method)
    }


@pytest.mark.parametrize(("client_class", "native_class", "expected_count"), CLIENTS)
def test_every_python_method_has_a_native_rust_method(
    client_class: type[object],
    native_class: type[object],
    expected_count: int,
) -> None:
    python_methods = set(generated_methods(client_class))
    native_methods = {name for name in dir(native_class) if not name.startswith("_")}

    assert len(python_methods) == expected_count
    assert python_methods == native_methods


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_class", "_native_class", "_expected_count"), CLIENTS)
async def test_every_python_method_dispatches_to_its_native_counterpart(
    client_class: type[object],
    _native_class: type[object],
    _expected_count: int,
) -> None:
    client = client_class(region="us-east-1")
    sentinel = object()
    spy = NativeSpy(sentinel)
    client._native = spy  # type: ignore[attr-defined]

    for name, method in generated_methods(client_class).items():
        signature = inspect.signature(method)
        required = {
            parameter_name: object()
            for parameter_name, parameter in signature.parameters.items()
            if parameter_name != "self" and parameter.default is inspect.Parameter.empty
        }

        result = await getattr(client, name)(**required)
        called_name, params = spy.calls.pop()

        assert called_name == name
        assert all(value in params.values() for value in required.values())
        if signature.return_annotation == "None":
            assert result is None
        else:
            assert result is sentinel

        # Exercise optional arguments as well, including keyword escapes.
        supplied = {
            parameter_name: object()
            for parameter_name in signature.parameters
            if parameter_name != "self"
        }
        await getattr(client, name)(**supplied)
        called_name, params = spy.calls.pop()
        assert called_name == name
        assert len(params) == len(supplied)
        assert all(value in params.values() for value in supplied.values())


@pytest.mark.parametrize(
    "service",
    [
        service
        for service in list_services()
        if not SELECTED_SERVICES or service.service_id in SELECTED_SERVICES
    ],
    ids=lambda service: service.service_id,
)
def test_native_properties_match_the_generated_stubs(service: object) -> None:
    package_name = getattr(service, "python_package")
    package = importlib.import_module(package_name)
    native = importlib.import_module(f"{package_name}._native")
    assert package.__file__ is not None
    stub = ast.parse(Path(package.__file__).with_name("_native.pyi").read_text())
    for node in stub.body:
        if not isinstance(node, ast.ClassDef):
            continue
        native_class = getattr(native, node.name)
        assert native_class.__module__ == native.__name__, (
            f"{package_name}.{node.name} must identify its importable native module"
        )
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and any(
                isinstance(decorator, ast.Name) and decorator.id == "property"
                for decorator in member.decorator_list
            ):
                assert hasattr(native_class, member.name), (
                    f"{package_name}.{node.name}.{member.name}"
                )


@pytest.mark.parametrize(
    "service",
    [
        service
        for service in list_services()
        if not SELECTED_SERVICES or service.service_id in SELECTED_SERVICES
    ],
    ids=lambda service: service.service_id,
)
def test_public_client_is_the_service_class_and_accepts_session(service: object) -> None:
    package = importlib.import_module(getattr(service, "python_package"))
    name = getattr(service, "client_class")
    public_class = getattr(rboto, name)
    assert public_class is getattr(package, name)
    assert public_class.__name__ == name
    session = Session(region="us-east-1")
    client = public_class(session=session)
    assert isinstance(client, public_class)
    factory = getattr(rboto, getattr(service, "python_name"))
    assert type(factory(session=session)) is public_class
    if getattr(service, "native_class") != name:
        assert getattr(package, getattr(service, "native_class")) is public_class
    with pytest.raises(ValueError, match="session cannot be combined"):
        public_class(session=session, region="eu-west-1")
