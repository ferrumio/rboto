from rboto_codegen.registry import get_service
from rboto_codegen.rust_crate import parse_builder_required_fields, parse_crate


def test_builder_defaults_do_not_imply_required_inputs() -> None:
    crate = parse_crate(get_service("bedrock-runtime"))
    document = crate.types["DocumentBlock"]
    assert {field.name for field in document.fields if not field.optional} == {"format", "name"}
    assert document.builder_required_fields == ("name",)
    assert crate.types["Message"].builder_required_fields == ("role", "content")
    assert crate.types["CachePointBlock"].builder_required_fields == ("type",)


def test_required_fields_follow_builder_order_and_normalize_rust_identifiers() -> None:
    source = '''
pub fn build(self) -> Result<Example, BuildError> {
    Ok(Example {
        defaulted: self.defaulted.unwrap_or_default(),
        r#type: self.r#type.ok_or_else(|| BuildError::missing_field(
            "r#type", "required",
        ))?,
        name: self.name.ok_or_else(|| BuildError::missing_field("name", "required"))?,
    })
}
'''
    assert parse_builder_required_fields(source) == ("type", "name")
    assert parse_builder_required_fields("pub struct Example { pub name: String }") == ()
