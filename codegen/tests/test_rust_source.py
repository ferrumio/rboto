"""Formatting fixtures for the supported upstream declaration grammar."""

from pathlib import Path

import pytest
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import get_service
from rboto_codegen.rust_crate import (
    parse_builder_required_fields,
    parse_crate,
    parse_error_variants,
    parse_fields,
)
from rboto_codegen.rust_source import RustParseError

STRUCT = """
/// Braces, generics and quotes in docs are not syntax: { < " }
#[non_exhaustive]
pub struct Example {
    #[deprecated(note = "use another field, { }")]
    pub r#type: ::std::option::Option<
        ::std::collections::HashMap<
            ::std::string::String,
            ::std::vec::Vec<::std::option::Option<::std::boxed::Box<crate::types::Node>>>,
        >,
    >,
    /* nested /* comment */ with misleading pub missing: () */
    pub count:
        i64,
    pub(crate) _request_id: ::std::option::Option<::std::string::String>,
}
"""


@pytest.mark.parametrize(
    "source",
    [
        STRUCT,
        STRUCT.replace("\n", "\n    "),
        """
pub struct Example { pub r#type: std::option::Option<
std::collections::HashMap<std::string::String,
std::vec::Vec<std::option::Option<std::boxed::Box<crate::types::Node>>>>>,
pub count: i64 }
""",
    ],
)
def test_struct_layout_does_not_change_public_fields(tmp_path: Path, source: str) -> None:
    path = tmp_path / "example.rs"
    path.write_text(source)
    fields = parse_fields(path)
    assert [(field.name, field.optional) for field in fields] == [("type", True), ("count", False)]
    assert fields[0].rust_name == "r#type"
    assert fields[0].rust_type == (
        "::std::collections::HashMap<::std::string::String, "
        "::std::vec::Vec<::std::option::Option<::std::boxed::Box<crate::types::Node>>>>"
    )
    assert fields[1].rust_type == "i64"


@pytest.mark.parametrize(
    "source",
    [
        "pub struct Example { pub value: ::std::vec::Vec< }",
        "pub struct Example { pub value: i32 pub other: i32 }",
        "pub struct Example { pub value: [u8; 4] }",
        "pub struct Example { fields!(); }",
        "pub struct Example<T> { pub value: T }",
        "pub struct Example(i32);",
        "pub struct Example {",
        "pub type Example = i32;",
    ],
)
def test_unsupported_structs_fail_with_the_source_path(tmp_path: Path, source: str) -> None:
    path = tmp_path / "changed_upstream.rs"
    path.write_text(source)
    with pytest.raises(RustParseError, match="changed_upstream.rs"):
        parse_fields(path)


def test_missing_source_is_not_an_empty_structure(tmp_path: Path) -> None:
    with pytest.raises(RustParseError, match="missing.rs"):
        parse_fields(tmp_path / "missing.rs")
    path = tmp_path / "empty.rs"
    path.write_text("pub struct Empty {}")
    assert parse_fields(path) == ()


def test_builder_scope_comments_and_multiline_signature() -> None:
    source = """
    impl ExampleBuilder {
        pub fn build (
            self,
        ) -> ::std::result::Result<
            crate::types::Example,
            ::aws_smithy_types::error::operation::BuildError,
        > {
            // BuildError::missing_field("not_a_field", "comment")
            Ok(Example {
                name: self.name.ok_or_else(|| BuildError ::
                    missing_field("name", "required"))?,
                defaulted: self.defaulted.unwrap_or_default(),
            })
        }
        pub fn unrelated(self) {
            BuildError::missing_field("outside_build", "not a requirement");
        }
    }
    """
    assert parse_builder_required_fields(source) == ("name",)
    with pytest.raises(RustParseError, match="literal field name"):
        parse_builder_required_fields(
            source.replace('"name", "required"', 'field_name(), "required"')
        )
    with pytest.raises(RustParseError, match="consume self"):
        parse_builder_required_fields(source.replace("self,", "&self,"))


@pytest.fixture
def crate_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "sdk"
    operation = root / "src/operation/example"
    operation.mkdir(parents=True)
    (operation / "_example_input.rs").write_text("pub struct ExampleInput {}")
    (operation / "_example_output.rs").write_text("pub struct ExampleOutput {}")
    (root / "src/types").mkdir()

    def find_crate(_descriptor: ServiceDescriptor) -> Path:
        return root

    monkeypatch.setattr("rboto_codegen.rust_crate.find_crate", find_crate)
    return root


def test_multiline_enum_payloads_and_generic_types(crate_source: Path) -> None:
    path = crate_source / "src/types/_choice.rs"
    path.write_text("""
    #[non_exhaustive]
    pub enum Choice {
        #[deprecated(note = "a comma, or braces {} must not split a variant")]
        Nodes(
            ::std::vec::Vec<
                ::std::boxed::Box<crate::types::Node>,
            >,
        ),
        Empty,
        Unknown(crate::primitives::UnknownVariantValue),
    }
    """)
    choice = parse_crate(get_service("sqs")).types["Choice"]
    assert choice.union
    assert choice.enum_variants == ("Nodes", "Empty", "Unknown")
    assert choice.variant_types == (
        ("Nodes", "::std::vec::Vec<::std::boxed::Box<crate::types::Node>>"),
    )
    for source in (
        "pub enum Choice { Nodes { value: i32 } }",
        "pub enum Choice { Nodes(i32, i32) }",
        "pub enum Choice { Nodes(i32), Nodes(i32) }",
    ):
        path.write_text(source)
        with pytest.raises(RustParseError, match="_choice.rs"):
            parse_crate(get_service("sqs"))


def test_missing_operation_files_and_builders_fail(crate_source: Path) -> None:
    path = crate_source / "src/types/_example.rs"
    path.write_text("pub struct Example { pub name: ::std::string::String }")
    with pytest.raises(RustParseError, match="missing public builder"):
        parse_crate(get_service("sqs"))
    path.unlink()
    (crate_source / "src/operation/example/_example_input.rs").unlink()
    with pytest.raises(RustParseError, match="_example_input.rs"):
        parse_crate(get_service("sqs"))


def test_modeled_result_is_not_confused_with_a_fallible_builder(crate_source: Path) -> None:
    (crate_source / "src/types/_result.rs").write_text("""
    pub struct Result { pub value: i32 }
    impl ResultBuilder {
        pub fn build(self) -> crate::types::Result {
            crate::types::Result { value: self.value.unwrap_or_default() }
        }
    }
    """)
    assert not parse_crate(get_service("sqs")).types["Result"].build_fallible


def test_string_enum_mapping_is_complete_and_independent_of_layout(crate_source: Path) -> None:
    path = crate_source / "src/types/_status.rs"
    source = """
    pub enum Status { Ready, Stopped, Unknown(crate::primitives::UnknownVariantValue) }
    impl Status {
        pub fn as_str(&self) -> &str {
            match self {
                Self::Ready
                    => "READY",
                Status::Stopped => "stopped",
                Self::Unknown(value) => value.as_str(),
            }
        }
    }
    """
    path.write_text(source)
    assert parse_crate(get_service("sqs")).types["Status"].enum_values == ("READY", "stopped")
    for changed in (
        source.replace('Status::Stopped => "stopped",', ""),
        source.replace('Status::Stopped => "stopped",', 'Status::Stopped => "READY",'),
        source.replace('"READY"', "NEW_CONSTANT"),
        source.replace("as_str(&self)", "renamed(&self)"),
    ):
        path.write_text(changed)
        with pytest.raises(RustParseError, match="_status.rs"):
            parse_crate(get_service("sqs"))


def test_error_enums_keep_all_modeled_variants_across_layout_changes() -> None:
    source = """
    pub enum ExampleError {
        #[deprecated(note = "example")]
        Rejected(
            crate::types::error::RejectedException,
        ),
        Throttled(crate::types::error::ThrottledException),
        Unhandled(crate::error::sealed_unhandled::Unhandled),
    }
    pub enum EmptyError { Unhandled(crate::error::Unhandled) }
    """
    assert parse_error_variants(source) == {
        "ExampleError": (
            ("Rejected", "RejectedException"),
            ("Throttled", "ThrottledException"),
        ),
        "EmptyError": (),
    }
    with pytest.raises(RustParseError, match="unsupported modeled error payload"):
        parse_error_variants(source.replace("crate::types::error::RejectedException", "NewAlias"))
