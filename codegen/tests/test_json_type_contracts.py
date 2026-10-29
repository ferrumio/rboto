"""Resolve generated recursive JSON types without requiring compiled extensions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TypeAliasType, get_type_hints

import pytest
from pydantic import TypeAdapter, ValidationError
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import list_services

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("service", list_services(), ids=lambda item: item.service_id)
def test_json_alias_resolves_at_runtime_and_builds_a_recursive_schema(
    service: ServiceDescriptor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Load the complete generated types module. ByteStream is irrelevant to JSON;
    # a stand-in keeps this contract check independent of native build availability.
    namespace = f"_json_contract_{service.python_name}"
    package = ModuleType(namespace)
    native = ModuleType(f"{namespace}._native")
    setattr(native, "ByteStream", object)
    monkeypatch.setitem(sys.modules, namespace, package)
    monkeypatch.setitem(sys.modules, native.__name__, native)
    source = (
        ROOT / "packages" / service.distribution_name / "python"
        / service.python_package / "types.py"
    )
    spec = importlib.util.spec_from_file_location(f"{namespace}.types", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec.loader.exec_module(module)
    alias = getattr(module, "JSONValue")
    assert isinstance(alias, TypeAliasType)
    exec(
        "async def operation(payload: JSONValue | None = None) -> JSONValue: ...",
        module.__dict__,
    )
    hints = get_type_hints(getattr(module, "operation"))
    assert hints == {"payload": alias | None, "return": alias}

    adapter = TypeAdapter[object](hints["return"])
    value: dict[str, object] = {"items": [None, True, 3, 1.5, "value", {"nested": []}]}
    assert adapter.validate_python(value, strict=True) == value
    schema = adapter.json_schema()
    assert "$defs" in schema and "JSONValue" in schema["$defs"]
    for invalid in ({1: "non-string key"}, {"nested": object()}, {"nested": {1, 2}}):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid, strict=True)
