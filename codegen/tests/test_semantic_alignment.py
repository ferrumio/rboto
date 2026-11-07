"""Fail generation on semantic drift, including shapes shared by many operations."""

from dataclasses import replace
from pathlib import Path

import pytest
from rboto_codegen import cli
from rboto_codegen.alignment import align
from rboto_codegen.generator import GenerationError, ServiceGenerator
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import get_service
from rboto_codegen.rust_crate import RustCrate, RustField, RustOperation, RustType, parse_crate
from rboto_codegen.smithy import SmithyService, load_model

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def sqs() -> tuple[SmithyService, RustCrate]:
    descriptor = get_service("sqs")
    return load_model(ROOT / "codegen/models/sqs.json", descriptor), parse_crate(descriptor)


def changed_field(
    fields: tuple[RustField, ...],
    name: str,
    *,
    rust_type: str | None = None,
    optional: bool | None = None,
) -> tuple[RustField, ...]:
    assert any(field.name == name for field in fields)
    return tuple(
        replace(
            field,
            rust_type=rust_type if rust_type is not None else field.rust_type,
            optional=optional if optional is not None else field.optional,
        )
        if field.name == name
        else field
        for field in fields
    )


@pytest.mark.parametrize(
    ("field_name", "rust_type", "path"),
    [
        ("body", "i64", "Message.body"),
        (
            "message_attributes",
            "::std::collections::HashMap<i32, crate::types::MessageAttributeValue>",
            "Message.message_attributes.key",
        ),
        (
            "message_attributes",
            "::std::collections::HashMap<::std::string::String, crate::types::Message>",
            "Message.message_attributes.value",
        ),
    ],
)
def test_nested_types_and_collection_targets_must_align(
    sqs: tuple[SmithyService, RustCrate],
    field_name: str,
    rust_type: str,
    path: str,
) -> None:
    service, crate = sqs
    original = crate.types["Message"]
    changed = replace(
        original, fields=changed_field(original.fields, field_name, rust_type=rust_type)
    )
    broken = replace(crate, types={**crate.types, original.name: changed})
    with pytest.raises(ValueError, match=path):
        align(service, broken).require_aligned("future-service")


def test_nested_optionality_and_builder_requiredness(sqs: tuple[SmithyService, RustCrate]) -> None:
    service, crate = sqs
    message = crate.types["Message"]
    message = replace(message, fields=changed_field(message.fields, "body", optional=False))
    required = crate.types["SendMessageBatchRequestEntry"]
    required = replace(required, builder_required_fields=())
    broken = replace(crate, types={**crate.types, message.name: message, required.name: required})
    report = align(service, broken)
    assert any("Message.body: optionality differs" in item for item in report.semantic_mismatches)
    assert any(
        "SendMessageBatchRequestEntry.id: builder requiredness differs" in item
        for item in report.semantic_mismatches
    )


def test_model_requiredness_change_is_detected(sqs: tuple[SmithyService, RustCrate]) -> None:
    service, crate = sqs
    shape = next(shape for shape in service.shapes.values() if shape.name == "Message")
    changed = replace(
        shape,
        members=tuple(
            replace(member, required=True) if member.name == "Body" else member
            for member in shape.members
        ),
    )
    model = replace(service, shapes={**service.shapes, shape.shape_id: changed})
    with pytest.raises(ValueError, match="Message.body: optionality differs"):
        align(model, crate).require_aligned("future-service")


def test_changed_enum_value_is_detected_even_when_variant_count_matches(
    sqs: tuple[SmithyService, RustCrate],
) -> None:
    service, crate = sqs
    original = crate.types["QueueAttributeName"]
    changed = replace(original, enum_values=(*original.enum_values[:-1], "ChangedWireValue"))
    broken = replace(crate, types={**crate.types, original.name: changed})
    with pytest.raises(ValueError, match="QueueAttributeName: modeled and Rust enum values differ"):
        align(service, broken).require_aligned("future-service")


def test_operation_optionality_and_primitive_width(sqs: tuple[SmithyService, RustCrate]) -> None:
    service, crate = sqs
    operation = crate.operations["send_message"]
    changed = replace(
        operation,
        input_fields=changed_field(
            operation.input_fields,
            "delay_seconds",
            rust_type="i64",
            optional=False,
        ),
    )
    report = align(
        service, replace(crate, operations={**crate.operations, operation.name: changed})
    )
    assert any(
        "send_message.input.delay_seconds: expected" in item for item in report.semantic_mismatches
    )
    assert any("lost Rust Option" in item for item in report.semantic_mismatches)


def test_missing_nested_shape_is_not_silently_empty(sqs: tuple[SmithyService, RustCrate]) -> None:
    service, crate = sqs
    broken = replace(
        crate, types={name: value for name, value in crate.types.items() if name != "Message"}
    )
    with pytest.raises(ValueError, match="receive_message.output.messages.member"):
        align(service, broken).require_aligned("future-service")


def test_representation_exceptions_are_exact_and_detect_stale_entries() -> None:
    descriptor = get_service("s3")
    service = load_model(ROOT / "codegen/models/s3.json", descriptor)
    crate = parse_crate(descriptor)
    extras, types = descriptor.alignment_extras, descriptor.alignment_types
    align(service, crate, extras, types).require_aligned("future-object-service")
    with pytest.raises(ValueError, match="expires"):
        align(service, crate).require_aligned("future-object-service")
    operation = crate.operations["get_object"]
    changed = replace(
        operation,
        output_fields=changed_field(
            operation.output_fields,
            "expires_string",
            rust_type="i64",
        ),
    )
    broken = replace(crate, operations={**crate.operations, operation.name: changed})
    with pytest.raises(ValueError, match="stale or changed alignment extra"):
        align(service, broken, extras, types).require_aligned("future-object-service")
    stale = replace(types[0], shape="future#NoLongerPresent")
    with pytest.raises(ValueError, match="unused alignment type override"):
        align(service, crate, extras, (stale,)).require_aligned("future-object-service")


def test_defaults_and_client_optional_traits_preserve_sdk_representation() -> None:
    descriptor = get_service("bedrock-runtime")
    service = load_model(ROOT / "codegen/models/bedrock-runtime.json", descriptor)
    crate = parse_crate(descriptor)
    align(service, crate).require_aligned("future-runtime")
    document = crate.types["DocumentBlock"]
    # A modeled default is not a builder-required argument.
    broken = replace(
        document, builder_required_fields=(*document.builder_required_fields, "format")
    )
    with pytest.raises(ValueError, match="DocumentBlock.format: builder requiredness"):
        align(
            service, replace(crate, types={**crate.types, document.name: broken})
        ).require_aligned("future-runtime")


def test_unions_sparse_collections_and_recursive_boxes() -> None:
    descriptor = get_service("dynamodb")
    service = load_model(ROOT / "codegen/models/dynamodb.json", descriptor)
    crate = parse_crate(descriptor)
    union = crate.types["AttributeValue"]
    # DDB's union recurses through both maps and lists; change only an inner type.
    variants = tuple(
        (name, "::std::vec::Vec<i64>" if name == "L" else payload)
        for name, payload in union.variant_types
    )
    broken = replace(union, variant_types=variants)
    with pytest.raises(ValueError, match="AttributeValue.L.member"):
        align(service, replace(crate, types={**crate.types, union.name: broken})).require_aligned(
            "future-store"
        )
    descriptor = get_service("qbusiness")
    service = load_model(ROOT / "codegen/models/qbusiness.json", descriptor)
    crate = parse_crate(descriptor)
    found = False
    for name, rust in crate.types.items():
        for field in rust.fields:
            if "::std::vec::Vec<::std::option::Option<" not in field.rust_type:
                continue
            found = True
            fields = changed_field(
                rust.fields,
                field.name,
                rust_type="::std::vec::Vec<::std::string::String>",
            )
            broken = replace(rust, fields=fields)
            report = align(service, replace(crate, types={**crate.types, name: broken}))
            assert any(
                "sparse collection optionality differs" in item
                for item in report.semantic_mismatches
            )
            break
    assert found


def test_generation_rejects_mismatch_before_writing(
    sqs: tuple[SmithyService, RustCrate],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, crate = sqs
    operation = crate.operations["send_message"]
    changed = replace(
        operation,
        input_fields=changed_field(
            operation.input_fields,
            "message_body",
            rust_type="i64",
        ),
    )
    broken = replace(crate, operations={**crate.operations, operation.name: changed})

    def fake_model(_path: Path, _descriptor: ServiceDescriptor) -> SmithyService:
        return service

    def fake_crate(_descriptor: ServiceDescriptor) -> RustCrate:
        return broken

    monkeypatch.setattr("rboto_codegen.generator.load_model", fake_model)
    monkeypatch.setattr("rboto_codegen.generator.parse_crate", fake_crate)
    descriptor = replace(get_service("sqs"), service_id="future-service")
    with pytest.raises(GenerationError, match="send_message.input.message_body"):
        ServiceGenerator(descriptor, tmp_path / "model.json", tmp_path).generate()
    assert not list(tmp_path.iterdir())


def test_member_only_drift_fails_even_when_operation_names_match(
    sqs: tuple[SmithyService, RustCrate],
) -> None:
    service, crate = sqs
    operation = crate.operations["send_message"]
    broken_operation: RustOperation = replace(operation, input_fields=operation.input_fields[1:])
    with pytest.raises(ValueError, match="send_message"):
        align(
            service,
            replace(
                crate,
                operations={**crate.operations, operation.name: broken_operation},
            ),
        ).require_aligned("future-service")


def test_report_command_rejects_member_only_drift(
    sqs: tuple[SmithyService, RustCrate],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, crate = sqs
    operation = crate.operations["send_message"]
    changed = replace(operation, input_fields=operation.input_fields[1:])

    def fake_model(_path: Path, _descriptor: ServiceDescriptor) -> SmithyService:
        return service

    def fake_crate(_descriptor: ServiceDescriptor) -> RustCrate:
        return replace(crate, operations={**crate.operations, operation.name: changed})

    monkeypatch.setattr("sys.argv", ["rboto-codegen", "report", "sqs"])
    monkeypatch.setattr(cli, "load_model", fake_model)
    monkeypatch.setattr(cli, "parse_crate", fake_crate)
    with pytest.raises(ValueError, match="send_message"):
        cli.main()


def test_event_error_sets_and_modeled_error_fields_must_match() -> None:
    descriptor = get_service("bedrock-runtime")
    service = load_model(ROOT / "codegen/models/bedrock-runtime.json", descriptor)
    crate = parse_crate(descriptor)
    variants = crate.event_errors["ConverseStreamOutputError"]
    changed = replace(
        crate,
        event_errors={
            **crate.event_errors,
            "ConverseStreamOutputError": variants[:-1],
        },
    )
    with pytest.raises(ValueError, match="converse_stream.output.stream: modeled event errors"):
        align(service, changed).require_aligned("future-runtime")
    changed = replace(
        crate,
        errors={
            **crate.errors,
            "ModelErrorException": changed_field(
                crate.errors["ModelErrorException"],
                "original_status_code",
                rust_type="::std::string::String",
            ),
        },
    )
    with pytest.raises(ValueError, match="ModelErrorException.original_status_code"):
        align(service, changed).require_aligned("future-runtime")


def test_unknown_union_variants_and_wrong_payloads_fail() -> None:
    descriptor = get_service("dynamodb")
    service = load_model(ROOT / "codegen/models/dynamodb.json", descriptor)
    crate = parse_crate(descriptor)
    union: RustType = crate.types["AttributeValue"]
    for changed in (
        replace(union, enum_variants=(*union.enum_variants, "NewUnmodeled")),
        replace(
            union,
            variant_types=tuple(
                (name, payload) for name, payload in union.variant_types if name != "S"
            ),
        ),
    ):
        with pytest.raises(ValueError, match="AttributeValue"):
            align(
                service,
                replace(
                    crate,
                    types={**crate.types, union.name: changed},
                ),
            ).require_aligned("future-store")
