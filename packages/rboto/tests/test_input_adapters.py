from collections.abc import AsyncIterator
from types import MappingProxyType

import pytest
from rboto.inputs import (
    EnumInputAdapter,
    InputAdapter,
    InputConversionError,
    _Member,
    _Schema,
    _Shape,
)

SCHEMA = _Schema({
    "Node": _Shape("structure", members={
        "name": _Member("smithy.api#String", True),
        "mode": _Member("Mode"),
        "children": _Member("Nodes"),
        "choice": _Member("Choice"),
        "metadata": _Member("smithy.api#Document"),
        "blob": _Member("smithy.api#Blob"),
    }),
    "Mode": _Shape("string", values=("ready", "done")),
    "Nodes": _Shape("sequence", item="Node", sparse=True),
    "Entries": _Shape("map", key="Mode", item="Node", sparse=True),
    "Choice": _Shape("union", members={
        "node": _Member("Node"),
        "empty": _Member("smithy.api#Unit"),
    }),
    "Events": _Shape("union", members={"node": _Member("Node")}, streaming=True),
})
NODE = InputAdapter[dict[str, object]](SCHEMA, "Node")


def test_recursive_conversion_preserves_sparse_values_and_does_not_mutate() -> None:
    original = {
        "name": "root", "mode": "ready", "blob": b"data", "choice": {"empty": None},
        "children": ({"name": "child", "mode": None}, None),
        "metadata": {"explicit_null": None, "values": (1, True)},
    }
    converted = NODE(MappingProxyType(original))
    assert converted == {
        "name": "root", "mode": "ready", "blob": b"data", "choice": {"empty": None},
        "children": [{"name": "child"}, None],
        "metadata": {"explicit_null": None, "values": [1, True]},
    }
    assert original["children"] == ({"name": "child", "mode": None}, None)
    entries = InputAdapter[dict[str, object]](SCHEMA, "Entries")
    assert entries({"ready": None, "done": {"name": "child"}}) == {
        "ready": None, "done": {"name": "child"},
    }


@pytest.mark.parametrize(
    ("value", "path"),
    [
        ({}, "Node.name"),
        ({"name": None}, "Node.name"),
        ({"name": "root", "mode": "future"}, "Node.mode"),
        ({"name": "root", "children": "invalid"}, "Node.children"),
        ({"name": "root", "children": [None, {"name": False}]}, "Node.children[1].name"),
        ({"name": "root", "choice": {"unknown": True}}, "Node.choice"),
        ({"name": "root", "choice": {"empty": None, "node": {"name": "child"}}}, "Node.choice"),
        ({"name": "root", "extra": "value"}, "Node"),
        ({"name": "root", "blob": object()}, "Node.blob"),
        ({"name": "root", "metadata": {"invalid": float("nan")}}, "Node.metadata[0]"),
    ],
)
def test_invalid_nested_values_fail_with_a_field_path(value: object, path: str) -> None:
    with pytest.raises(InputConversionError) as error:
        NODE(value)
    assert error.value.path == path


def test_enum_conversion_and_type_guard() -> None:
    mode = EnumInputAdapter[str](SCHEMA, "Mode")
    assert mode("ready") == "ready"
    assert mode.is_known("done")
    assert not mode.is_known("future")
    assert not mode.is_known(None)
    with pytest.raises(InputConversionError, match="unknown enum"):
        mode("future")
    with pytest.raises(InputConversionError, match=r"Entries\[0\].key"):
        InputAdapter[object](SCHEMA, "Entries")({"future": None})


def test_cycles_fail_but_shared_values_are_allowed() -> None:
    value: dict[str, object] = {"name": "root"}
    value["children"] = [value]
    with pytest.raises(InputConversionError, match="cyclic"):
        NODE(value)
    child = {"name": "child"}
    assert NODE({"name": "root", "children": [child, child]}) == {
        "name": "root", "children": [{"name": "child"}, {"name": "child"}],
    }


def test_native_values_are_converted_explicitly() -> None:
    class NativeNode:
        calls = 0

        def to_dict(self) -> dict[str, object]:
            self.calls += 1
            return {"name": "root", "mode": None}

    native = NativeNode()
    assert NODE(native) == {"name": "root"}
    assert native.calls == 1


def test_native_stream_ownership_is_not_touched_on_conversion_failure() -> None:
    schema = _Schema({
        "Response": _Shape("structure", members={"body": _Member("Body")}),
        "Body": _Shape("blob", streaming=True),
    })

    class NativeResponse:
        def to_dict(self) -> dict[str, object]:
            raise AssertionError("conversion must not take ownership of the stream")

    adapter = InputAdapter[dict[str, object]](schema, "Response")
    with pytest.raises(InputConversionError, match="explicit mapping"):
        adapter(NativeResponse())
    assert adapter({"body": b"already read"}) == {"body": b"already read"}


@pytest.mark.asyncio
async def test_event_conversion_is_lazy_and_validates_each_event() -> None:
    consumed: list[int] = []

    async def events() -> AsyncIterator[object]:
        consumed.append(1)
        yield {"node": {"name": "first", "mode": None}}
        consumed.append(2)
        yield {"unknown": True}

    adapter = InputAdapter[AsyncIterator[dict[str, object]]](SCHEMA, "Events")
    stream = adapter(events())
    assert not consumed
    assert await anext(stream) == {"node": {"name": "first"}}
    assert consumed == [1]
    with pytest.raises(InputConversionError) as error:
        await anext(stream)
    assert error.value.path == "Events[1]"
