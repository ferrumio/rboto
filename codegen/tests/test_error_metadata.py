import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from rboto_codegen.alignment import align
from rboto_codegen.generator import GenerationError, ServiceGenerator
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import get_service, list_services
from rboto_codegen.rust_crate import parse_crate, parse_error_variants
from rboto_codegen.rust_source import RustParseError, error_codes, tokens
from rboto_codegen.smithy import load_model

ROOT = Path(__file__).parents[2]


class ErrorRenderer(ServiceGenerator):
    def render_types(self) -> str:
        return self._render_python_types()

    def render_errors(self) -> str:
        return self._render_error_bindings()


def test_error_enum_parser_ignores_unmodeled_variants() -> None:
    source = """
pub enum ExampleError {
    /// a modeled error
    Rejected(crate::types::error::RejectedException),
    Unhandled(crate::error::Unhandled),
}
"""
    assert parse_error_variants(source) == {
        "ExampleError": (("Rejected", "RejectedException"),),
    }


def test_error_details_are_generated_for_an_unregistered_service_name() -> None:
    descriptor = replace(get_service("bedrock-runtime"), service_id="unregistered-runtime")
    generator = ErrorRenderer(
        descriptor, ROOT / "codegen/models/bedrock-runtime.json", ROOT,
    )
    # The same contract is used by ordinary operations and modeled stream errors.
    types = ast.parse(generator.render_types())
    schemas = {
        node.name: node for node in types.body if isinstance(node, ast.ClassDef)
    }
    expected = {
        "ModelErrorDetails": {"original_status_code": "int", "resource_name": "str"},
        "ModelStreamErrorDetails": {"original_status_code": "int", "original_message": "str"},
    }
    for name, expected_fields in expected.items():
        fields = {
            node.target.id: ast.unparse(node.annotation)
            for node in schemas[name].body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        assert fields == expected_fields
    bindings = generator.render_errors()
    assert "ModelStreamErrorException(value)" in bindings
    assert "ModelErrorException(value)" in bindings
    assert 'set_item("original_status_code"' in bindings
    assert 'set_item("message"' not in bindings
    assert 'ModelStreamErrorException(_) => Some("ModelStreamErrorException")' in bindings


@pytest.mark.parametrize("wrapped", [False, True])
def test_decoder_error_codes_ignore_formatting_and_comments(wrapped: bool) -> None:
    expression = """crate :: operation :: example :: ExampleError :: Rejected ({
        let output = builder.meta(generic); output.build()
    })"""
    if wrapped:
        expression = "{ " + expression + " }"
    source = f"""
        // match error_code {{ "ignored" => ignored() }}
        match error_code {{
            "Different.WireCode" => {expression},
            _ => crate::operation::example::ExampleError::generic(generic),
        }}
    """
    assert error_codes(tokens(source), "example", "ExampleError") == (
        ("Different.WireCode", "Rejected"),
    )


@pytest.mark.parametrize("source", [
    "match other_code { _ => generic() }",
    'match error_code { "Code" if condition => generic() }',
    'match error_code { variable => generic() }',
    'match error_code { "Code" => different::Error::Rejected(value) }',
    'match error_code { "" => crate::operation::example::ExampleError::Rejected(value) }',
    '''match error_code {
        "Code" => crate::operation::example::ExampleError::Rejected(value),
        "Code" => crate::operation::example::ExampleError::Rejected(value),
    }''',
])
def test_unsupported_or_ambiguous_decoder_mappings_fail(source: str) -> None:
    with pytest.raises(RustParseError):
        error_codes(tokens(source), "example", "ExampleError")


@pytest.mark.parametrize(("service_id", "shape_name", "wire_code"), [
    ("sqs", "QueueDoesNotExist", "AWS.SimpleQueueService.NonExistentQueue"),
    ("sns", "InvalidParameterValueException", "ParameterValueInvalid"),
    ("cloudtrail", "AccessDeniedException", "ResourceAccessDenied"),
])
def test_query_aliases_are_retained_and_checked_in_the_active_protocol(
    service_id: str, shape_name: str, wire_code: str,
) -> None:
    descriptor = get_service(service_id)
    service = load_model(ROOT / f"codegen/models/{service_id}.json", descriptor)
    shape = next(shape for shape in service.shapes.values() if shape.name == shape_name)
    assert shape.query_error_code == wire_code
    crate = parse_crate(descriptor)
    changed = replace(service, shapes={
        **service.shapes, shape.shape_id: replace(shape, query_error_code="ChangedAlias"),
    })
    if service_id == "cloudtrail":
        # A dormant Query trait must not override the active JSON wire code.
        align(changed, crate).require_aligned(service_id)
    else:
        with pytest.raises(ValueError, match="error code differs"):
            align(changed, crate).require_aligned("future-service")


@pytest.mark.parametrize("code", ["", " ", 7, None, ["alias"]])
def test_malformed_model_alias_is_rejected(tmp_path: Path, code: object) -> None:
    descriptor = get_service("sqs")
    model = cast(dict[str, object], json.loads((ROOT / "codegen/models/sqs.json").read_text()))
    shapes = cast(dict[str, dict[str, object]], model["shapes"])
    shape = next(shape for name, shape in shapes.items() if name.endswith("#QueueDoesNotExist"))
    traits = cast(dict[str, object], shape["traits"])
    traits["aws.protocols#awsQueryError"] = {"code": code}
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model))
    descriptor = replace(descriptor, model_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="awsQueryError.code"):
        load_model(path, descriptor)


def test_changed_alias_blocks_generation_before_writing(tmp_path: Path) -> None:
    descriptor = get_service("sqs")
    model = cast(dict[str, object], json.loads((ROOT / "codegen/models/sqs.json").read_text()))
    shapes = cast(dict[str, dict[str, object]], model["shapes"])
    shape = next(shape for name, shape in shapes.items() if name.endswith("#QueueDoesNotExist"))
    traits = cast(dict[str, object], shape["traits"])
    traits["aws.protocols#awsQueryError"] = {"code": "ChangedAlias"}
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model))
    descriptor = replace(descriptor, model_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(GenerationError, match="error code differs"):
        ServiceGenerator(descriptor, path, output).generate()
    assert not list(output.iterdir())


def test_generation_uses_modeled_identity_for_a_renamed_service() -> None:
    descriptor = replace(get_service("sqs"), service_id="future-queue-service")
    generator = ErrorRenderer(descriptor, ROOT / "codegen/models/sqs.json", ROOT)
    bindings = generator.render_errors()
    assert 'QueueDoesNotExist(_) => Some("QueueDoesNotExist")' in bindings
    assert 'KmsAccessDenied(_) => Some("KmsAccessDenied")' in bindings
    assert "AWS.SimpleQueueService.NonExistentQueue" not in bindings
    assert "_ => None" in bindings


@pytest.mark.parametrize("descriptor", list_services(), ids=lambda service: service.service_id)
def test_all_generated_error_identities_cover_the_pinned_decoders(
    descriptor: ServiceDescriptor,
) -> None:
    generator = ErrorRenderer(
        descriptor, ROOT / f"codegen/models/{descriptor.service_id}.json", ROOT,
    )
    bindings = generator.render_errors()
    models = {
        shape.name.replace("_", "").lower(): shape
        for shape in generator.service.shapes.values() if shape.error
    }
    rust_errors = {
        error for codes in generator.crate.operation_error_codes.values() for _, error in codes
    } | {
        error for variants in generator.crate.event_errors.values() for _, error in variants
    }
    for error in rust_errors:
        shape = models[error.replace("_", "").lower()]
        assert f'Some("{shape.name}")' in bindings
