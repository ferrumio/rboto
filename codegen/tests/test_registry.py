from pathlib import Path

import pytest
from rboto_codegen import get_service, list_services, load_service


def test_service_configs_are_discovered() -> None:
    assert [service.service_id for service in list_services()] == [
        "dynamodb",
        "s3",
        "sns",
        "sqs",
    ]


def test_s3_descriptor_is_loaded_from_toml() -> None:
    descriptor = get_service("s3")
    assert descriptor.distribution_name == "rboto-s3"
    assert descriptor.rust_crate == "aws-sdk-s3"
    assert descriptor.force_path_style_for_custom_endpoint


def test_all_services_enable_native_outputs() -> None:
    assert all(service.native_outputs for service in list_services())


def test_filename_must_match_service_id(tmp_path: Path) -> None:
    config = tmp_path / "wrong.toml"
    config.write_text(
        """
service_id = "right"
python_name = "right"
distribution_name = "rboto-right"
python_package = "rboto_right"
client_class = "RightClient"
adapter_crate = "rboto-right-native"
rust_crate = "aws-sdk-right"
rust_module = "aws_sdk_right"
rust_crate_version = "1.0.0"
model_url = "https://example.invalid/right.json"
model_sha256 = "00"
aws_model_hash = "00"
smithy_codegen_revision = "test"
""".strip()
    )
    with pytest.raises(ValueError, match="does not match"):
        load_service(config)
