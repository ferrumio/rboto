import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from rboto.inputs import InputConversionError
from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import get_service
from rboto_codegen.rust_crate import RustCrate, RustField, RustType


class AdapterGenerator(ServiceGenerator):
    def render_inputs(self) -> str:
        return self._render_python_inputs()


def future_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, added_value: bool = False,
) -> dict[str, object]:
    """Use an unrelated synthetic service, including a model version change."""
    enum_values = ("ready", "done", "new") if added_value else ("ready", "done")
    model: dict[str, object] = {
        "smithy": "2.0",
        "shapes": {
            "smithy.api#Unit": {"type": "structure"},
            "future#Service": {
                "type": "service", "version": "1",
                "operations": [{"target": "future#Submit"}],
            },
            "future#Submit": {"type": "operation", "input": {"target": "future#Node"}},
            "future#Node": {"type": "structure", "members": {
                "Label": {"target": "smithy.api#String", "traits": {"smithy.api#required": {}}},
                "Mode": {"target": "future#Mode"},
                "Children": {"target": "future#Nodes"},
                "Choice": {"target": "future#Choice"},
                "MD5Hash": {"target": "smithy.api#String"},
                "First": {"target": "future#HTTPMode"},
                "Second": {"target": "future#HttpMode"},
                "Data": {"target": "future#Dict"},
            }},
            "future#Nodes": {"type": "list", "member": {"target": "future#Node"}},
            "future#Mode": {"type": "enum", "members": {
                name: {"traits": {"smithy.api#enumValue": name}} for name in enum_values
            }},
            "future#Choice": {"type": "union", "members": {
                "Node": {"target": "future#Node"},
                "Nothing": {"target": "smithy.api#Unit"},
            }},
            "future#HTTPMode": {"type": "string"},
            "future#HttpMode": {"type": "string"},
            "future#Dict": {
                "type": "map", "key": {"target": "smithy.api#String"},
                "value": {"target": "future#Node"},
            },
            "future#Unused": {"type": "string"},
        },
    }
    raw = json.dumps(model).encode()
    path = tmp_path / "future.json"
    path.write_bytes(raw)
    descriptor = replace(
        get_service("sqs"), service_id="future", python_package="rboto_future",
        model_sha256=hashlib.sha256(raw).hexdigest(),
    )
    # A concrete SDK spelling can differ from the Smithy snake_case name.
    rust = RustCrate(tmp_path, {}, {
        "Node": RustType(
            "Node", (RustField("md5hash", "md5hash", "::std::string::String", True),),
            (), False, False, True,
        ),
    })
    def parse_test_crate(_descriptor: ServiceDescriptor) -> RustCrate:
        return rust

    monkeypatch.setattr("rboto_codegen.generator.parse_crate", parse_test_crate)
    generator = AdapterGenerator(descriptor, path, tmp_path)
    source = generator.render_inputs()
    assert source == generator.render_inputs()
    assert "_builtins.dict" in source
    package = ModuleType("rboto_future")
    types = ModuleType("rboto_future.types")
    monkeypatch.setitem(sys.modules, "rboto_future", package)
    monkeypatch.setitem(sys.modules, "rboto_future.types", types)
    namespace: dict[str, object] = {"__package__": "rboto_future"}
    exec(compile(source, "<future inputs>", "exec"), namespace)
    return namespace


def test_new_service_recursion_naming_and_enum_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapters = future_adapters(tmp_path, monkeypatch)
    node = cast(Callable[[object], object], adapters["node"])
    assert node({
        "label": "root", "mode": "ready", "md5hash": "abc",
        "children": ({"label": "child", "mode": None},),
        "choice": {"nothing": None}, "data": {"entry": {"label": "mapped"}},
    }) == {
        "label": "root", "mode": "ready", "md5hash": "abc",
        "children": [{"label": "child"}],
        "choice": {"nothing": None}, "data": {"entry": {"label": "mapped"}},
    }
    assert "http_mode" in adapters and "http_mode_value" in adapters
    assert "unused" not in adapters
    with pytest.raises(InputConversionError, match="unknown enum"):
        node({"label": "root", "mode": "new"})
    with pytest.raises(InputConversionError, match="unknown union"):
        node({"label": "root", "choice": {"unknown": True}})
    updated = future_adapters(tmp_path, monkeypatch, added_value=True)
    new_node = cast(Callable[[object], object], updated["node"])
    assert new_node({"label": "root", "mode": "new"}) == {"label": "root", "mode": "new"}
