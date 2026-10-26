from pathlib import Path

import pytest
from rboto_codegen.alignment import align
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import get_service, list_services
from rboto_codegen.rust_crate import parse_crate
from rboto_codegen.smithy import load_model

MODEL_DIR = Path(__file__).parents[1] / "models"


def test_s3_model_and_crate_expose_the_same_operations() -> None:
    descriptor = get_service("s3")
    service = load_model(MODEL_DIR / "s3.json", descriptor)
    report = align(service, parse_crate(descriptor))

    assert len(service.operations) == 112
    assert not report.smithy_only_operations
    assert not report.rust_only_operations


@pytest.mark.parametrize("descriptor", list_services(), ids=lambda service: service.service_id)
def test_all_registered_services_align(descriptor: ServiceDescriptor) -> None:
    service = load_model(MODEL_DIR / f"{descriptor.service_id}.json", descriptor)
    crate = parse_crate(descriptor)
    report = align(service, crate)
    assert service.operations
    assert not report.smithy_only_operations
    assert not report.rust_only_operations
    if descriptor.service_id == "s3":
        # The Rust SDK retains the raw Expires header alongside its parsed timestamp.
        assert {item.name for item in report.mismatched} == {"get_object", "head_object"}
        assert all(
            item.rust_only_outputs == ("expires_string",)
            and not (item.smithy_only_inputs or item.rust_only_inputs or item.smithy_only_outputs)
            for item in report.mismatched
        )
    else:
        assert not report.mismatched


def test_sqs_proves_the_same_pipeline_accepts_another_service() -> None:
    descriptor = get_service("sqs")
    service = load_model(MODEL_DIR / "sqs.json", descriptor)
    report = align(service, parse_crate(descriptor))

    assert len(service.operations) == 23
    assert len(report.aligned) == 23
    assert not report.mismatched
    assert not report.smithy_only_operations
    assert not report.rust_only_operations


def test_sns_model_and_crate_expose_the_same_operations() -> None:
    descriptor = get_service("sns")
    service = load_model(MODEL_DIR / "sns.json", descriptor)
    crate = parse_crate(descriptor)
    report = align(service, crate)

    assert len(service.operations) == 42
    assert len(report.aligned) == 42
    assert crate.operations["get_sms_attributes"].output_type == "GetSmsAttributesOutput"
    assert not report.mismatched
    assert not report.smithy_only_operations
    assert not report.rust_only_operations
