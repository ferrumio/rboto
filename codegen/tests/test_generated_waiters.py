from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from rboto._waiters import Acceptor, WaiterSpec, wait
from rboto.waiters import WaiterConfig, WaiterFailedError, WaiterTimeoutError
from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import get_service, list_services, load_service, service_config_dir
from rboto_codegen.smithy import SmithyService, load_model
from rboto_codegen.waiter_metadata import parse_waiters
from rboto_codegen.waiters import resolve_waiters

ROOT = Path(__file__).parents[2]


@pytest.fixture
def modeled(tmp_path: Path) -> tuple[ServiceDescriptor, SmithyService]:
    model: dict[str, object] = {
        "smithy": "2.0",
        "shapes": {
            "test#Service": {
                "type": "service", "version": "1",
                "operations": [{"target": "test#Describe"}],
            },
            "test#Describe": {
                "type": "operation",
                "input": {"target": "test#Input"}, "output": {"target": "test#Output"},
                "errors": [{"target": "test#NotFound"}],
                "traits": {"smithy.waiters#waitable": {"Ready": {
                    "minDelay": 3,
                    "acceptors": [{"state": "success", "matcher": {"output": {
                        "path": "class.Status", "expected": "READY", "comparator": "stringEquals",
                    }}}],
                }}},
            },
            "test#Input": {"type": "structure", "members": {
                "Name": {"target": "smithy.api#String", "traits": {"smithy.api#required": {}}},
            }},
            "test#Output": {"type": "structure", "members": {
                "class": {"target": "test#Child"}, "Items": {"target": "test#Items"},
                "Count": {"target": "smithy.api#Integer"},
                "Complete": {"target": "smithy.api#Boolean"},
            }},
            "test#Child": {"type": "structure", "members": {
                "Status": {"target": "test#Status"},
            }},
            "test#Items": {"type": "list", "member": {"target": "test#Child"}},
            "test#Status": {"type": "enum", "members": {
                "Ready": {"traits": {"smithy.api#enumValue": "READY"}},
                "Pending": {"traits": {"smithy.api#enumValue": "PENDING"}},
                "Failed": {"traits": {"smithy.api#enumValue": "FAILED"}},
            }},
            "test#NotFound": {"type": "structure", "traits": {"smithy.api#error": "client"}},
        },
    }
    raw = json.dumps(model).encode()
    path = tmp_path / "model.json"
    path.write_bytes(raw)
    descriptor = replace(
        get_service("sns"), service_id="future-service",
        model_sha256=hashlib.sha256(raw).hexdigest(), waiters=(),
    )
    return descriptor, load_model(path, descriptor)


def output_spec(
    path: str = "class.Status", expected: str = "READY", comparator: str = "stringEquals",
) -> dict[str, object]:
    return {"Ready": {"operation": "Describe", "acceptors": [{
        "state": "success", "matcher": {"output": {
            "path": path, "expected": expected, "comparator": comparator,
        }},
    }]}}


def test_smithy_defaults_and_native_keyword_names(
    modeled: tuple[ServiceDescriptor, SmithyService],
) -> None:
    descriptor, service = modeled
    native = resolve_waiters(descriptor, service, {"describe"}, lambda s, m: m.rust_name)
    mapping = resolve_waiters(descriptor, service, set(), lambda s, m: m.rust_name)
    assert len(native) == 1 and native[0].name == "ready" and native[0].delay == 3
    assert native[0].returns_output and not native[0].returns_none
    assert native[0].acceptors[0].expression == (
        "subexpression", None, (("field", "class_", ()), ("field", "status", ())),
    )
    assert mapping[0].acceptors[0].expression == (
        "subexpression", None, (("field", "class", ()), ("field", "status", ())),
    )


def test_descriptor_supplement_uses_same_schema_and_overrides_named_trait(
    modeled: tuple[ServiceDescriptor, SmithyService], tmp_path: Path,
) -> None:
    path = tmp_path / "sns.toml"
    path.write_text((service_config_dir() / "sns.toml").read_text() + """
[waiters.Ready]
operation = "Describe"
minDelay = 7
[[waiters.Ready.acceptors]]
state = "success"
matcher.errorType = "NotFound"
""")
    descriptor, service = modeled
    descriptor = replace(descriptor, waiters=load_service(path).waiters)
    resolved = resolve_waiters(descriptor, service, {"describe"}, lambda s, m: m.rust_name)
    assert len(resolved) == 1 and resolved[0].delay == 7
    assert not resolved[0].returns_output and resolved[0].returns_none


@pytest.mark.parametrize("raw, error", [
    ({"Ready": {"acceptors": []}}, "operation"),
    ({"Ready": {"operation": "Describe", "acceptors": []}}, "needs acceptors"),
    ({"Ready": {"operation": "Describe", "minDelay": True}}, "delays"),
    ({"Ready": {"operation": "Describe", "minDelay": 5, "maxDelay": 1}}, "delays"),
    ({"Ready": {"operation": "Describe", "typo": True}}, "unknown waiter fields"),
    ({"Ready": {"operation": "Describe", "acceptors": [{
        "state": "almost", "matcher": {"success": True},
    }]}}, "invalid waiter acceptor"),
    ({"Ready": {"operation": "Describe", "acceptors": [{
        "state": "retry", "matcher": {"success": True},
    }]}}, "success acceptor"),
    ({"Ready": {"operation": "Describe", "acceptors": [{
        "state": "success", "matcher": {"status": 200},
    }]}}, "unsupported waiter matcher"),
    ({"Ready": {"operation": "Describe", "acceptors": [{
        "state": "success", "matcher": {"success": True, "errorType": "NotFound"},
    }]}}, "exactly one"),
    (output_spec(comparator="matchesRegex"), "comparator"),
    (output_spec(expected="yes", comparator="booleanEquals"), "true/false"),
])
def test_invalid_metadata_is_rejected(raw: object, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        parse_waiters(raw)


@pytest.mark.parametrize("raw,error", [
    (output_spec("Missing"), "unknown waiter field"),
    (output_spec("Count.Status"), "non-structure"),
    (output_spec("Items[].Missing", comparator="anyStringEquals"), "unknown waiter field"),
    (output_spec("Items[0].Status", "TYPO"), "unknown waiter enum"),
    (output_spec("Count"), "incompatible waiter comparator"),
    (output_spec("Complete", "true", "stringEquals"), "incompatible waiter comparator"),
    (output_spec("class.Status", "READY", "allStringEquals"), "requires a list"),
    (output_spec("length(Complete)", "true", "booleanEquals"), "requires a collection"),
    (output_spec("Count == 'one'", "true", "booleanEquals"), "incompatible"),
    (output_spec("class.Status == 'TYPO'", "true", "booleanEquals"), "unknown waiter enum"),
    (output_spec("class.Status > 'READY'", "true", "booleanEquals"), "requires numbers"),
    (output_spec("Items[?Status]", "READY", "anyStringEquals"), "predicate must be boolean"),
    (output_spec("class.Status || Complete", "true", "booleanEquals"), "require booleans"),
    (output_spec("sort(Items)", "READY", "anyStringEquals"), "unsupported waiter expression"),
    ({"Ready": {"operation": "Absent", "acceptors": [{
        "state": "success", "matcher": {"success": True},
    }]}}, "unknown waiter polling"),
    ({"Ready": {"operation": "Describe", "acceptors": [{
        "state": "success", "matcher": {"errorType": "InventedError"},
    }]}}, "unmodeled waiter error"),
])
def test_invalid_semantics_fail_generation(
    modeled: tuple[ServiceDescriptor, SmithyService], raw: object, error: str,
) -> None:
    descriptor, service = modeled
    descriptor = replace(descriptor, waiters=parse_waiters(raw))
    with pytest.raises(ValueError, match=error):
        resolve_waiters(descriptor, service, {"describe"}, lambda s, m: m.rust_name)


def test_streaming_outputs_and_control_parameter_collisions_are_rejected(
    modeled: tuple[ServiceDescriptor, SmithyService],
) -> None:
    descriptor, service = modeled
    output = service.shapes["test#Output"]
    streamed = replace(service, shapes={
        **service.shapes, "test#Output": replace(output, streaming=True),
    })
    with pytest.raises(ValueError, match="cannot own streams"):
        resolve_waiters(descriptor, streamed, {"describe"}, lambda s, m: m.rust_name)
    input_shape = service.shapes["test#Input"]
    collided = replace(service, shapes={
        **service.shapes, "test#Input": replace(input_shape, members=(
            replace(input_shape.members[0], rust_name="waiter_config"),
        )),
    })
    with pytest.raises(ValueError, match="collides"):
        resolve_waiters(descriptor, collided, {"describe"}, lambda s, m: m.rust_name)


@pytest.mark.asyncio
@pytest.mark.parametrize("expression,expected,status", [
    ("Complete || class.Status == 'READY'", "true", "READY"),
    ("Complete && class.Status == 'READY'", "false", "PENDING"),
])
async def test_known_boolean_branch_can_decide_with_another_field_missing(
    modeled: tuple[ServiceDescriptor, SmithyService], expression: str, expected: str, status: str,
) -> None:
    descriptor, service = modeled
    descriptor = replace(
        descriptor, waiters=parse_waiters(output_spec(expression, expected, "booleanEquals")),
    )
    spec = resolve_waiters(descriptor, service, {"describe"}, lambda s, m: m.rust_name)[0]
    acceptor = spec.acceptors[0]
    output = SimpleNamespace(complete=None, class_=SimpleNamespace(status=status))

    async def poll() -> object:
        return output

    assert await wait(
        poll, {}, WaiterSpec("ready", 0, (
            Acceptor(acceptor.state, acceptor.matcher, acceptor.expected,
                     acceptor.expression, acceptor.comparator),
        )), WaiterConfig(max_attempts=1),
    ) is output


@pytest.mark.parametrize("descriptor", list_services(), ids=lambda item: item.service_id)
def test_generated_catalog_preserves_strict_signatures_and_native_alignment(
    descriptor: ServiceDescriptor,
) -> None:
    generator = ServiceGenerator(
        descriptor, ROOT / "codegen/models" / f"{descriptor.service_id}.json", ROOT,
    )
    package = (
        ROOT / "packages" / descriptor.distribution_name / "python" / descriptor.python_package
    )
    client = next(node for node in ast.parse((package / "client.py").read_text()).body
                  if isinstance(node, ast.ClassDef))
    operations = {node.name: node for node in client.body if isinstance(node, ast.AsyncFunctionDef)}
    assert operations.keys() == {op.rust_name for op in generator.service.operations}
    path = package / "waiters.py"
    assert path.exists() == bool(generator.waiters)
    if not generator.waiters:
        assert not any(isinstance(n, ast.FunctionDef) and n.name == "waiters" for n in client.body)
        return
    namespace = next(node for node in ast.parse(path.read_text()).body
                     if isinstance(node, ast.ClassDef))
    methods = {node.name: node for node in namespace.body if isinstance(node, ast.AsyncFunctionDef)}
    assert methods.keys() == {waiter.name for waiter in generator.waiters}
    for waiter in generator.waiters:
        method, operation = methods[waiter.name], operations[waiter.operation]
        args = method.args
        assert args.kwarg is None and len(args.args) == 1
        assert args.kwonlyargs[-1].arg == "waiter_config"
        assert [ast.dump(arg) for arg in args.kwonlyargs[:-1]] == [
            ast.dump(arg) for arg in operation.args.kwonlyargs
        ]
        assert method.returns is not None and operation.returns is not None
        expected = (
            ast.unparse(operation.returns) + (" | None" if waiter.returns_none else "")
            if waiter.returns_output else "None"
        )
        assert ast.unparse(method.returns) == expected
        assert "#waiters" in (ast.get_docstring(method) or "")


@pytest.mark.asyncio
async def test_kinesis_composite_terminal_condition_uses_native_fields_without_to_dict() -> None:
    generator = ServiceGenerator(
        get_service("dynamodb"), ROOT / "codegen/models/dynamodb.json", ROOT,
    )
    spec = next(w for w in generator.waiters if w.name == "kinesis_streaming_destination_active")
    resolved = spec.acceptors[1]
    acceptor = Acceptor(
        resolved.state, resolved.matcher, resolved.expected,
        resolved.expression, resolved.comparator,
    )
    for states, failure in (
        ([], False), (["ENABLING"], False), (["DISABLED", "ENABLE_FAILED"], True),
        (["ACTIVE", "DISABLED"], False), ([None, "DISABLED"], False),
    ):
        output = SimpleNamespace(kinesis_data_stream_destinations=[
            SimpleNamespace(destination_status=state) for state in states
        ])
        async def poll() -> object:
            return output

        with pytest.raises(WaiterFailedError if failure else WaiterTimeoutError):
            await wait(
                poll, {}, WaiterSpec("terminal", 0, (acceptor,)),
                WaiterConfig(max_attempts=1),
            )
    output = SimpleNamespace(kinesis_data_stream_destinations=None)
    with pytest.raises(WaiterTimeoutError):
        await wait(poll, {}, WaiterSpec("terminal", 0, (acceptor,)), WaiterConfig(max_attempts=1))
