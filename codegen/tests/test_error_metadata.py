import ast
from dataclasses import replace
from pathlib import Path

from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.registry import get_service
from rboto_codegen.rust_crate import parse_error_variants

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
