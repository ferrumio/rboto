import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from rboto_codegen.registry import get_service
from rboto_codegen.smithy import load_model, to_snake_case


def test_nested_resource_operations_are_complete_and_deduplicated(tmp_path: Path) -> None:
    model = {
        "smithy": "2.0",
        "shapes": {
            "test#Service": {
                "type": "service",
                "version": "1",
                "resources": [{"target": "test#Parent"}],
                "operations": [{"target": "test#Get"}],
            },
            "test#Parent": {
                "type": "resource",
                "read": {"target": "test#Get"},
                "collectionOperations": [{"target": "test#List"}],
                "resources": [{"target": "test#Child"}],
            },
            "test#Child": {
                "type": "resource",
                "create": {"target": "test#Create"},
                "operations": [{"target": "test#Get"}],
            },
            **{f"test#{name}": {"type": "operation"} for name in ("Get", "List", "Create")},
            "test#Choice": {
                "type": "enum",
                "members": {
                    "ONE": {"traits": {"smithy.api#enumValue": "one"}},
                },
            },
        },
    }
    raw = json.dumps(model).encode()
    path = tmp_path / "model.json"
    path.write_bytes(raw)
    descriptor = replace(get_service("sns"), model_sha256=hashlib.sha256(raw).hexdigest())
    service = load_model(path, descriptor)
    assert {operation.name for operation in service.operations} == {"Get", "List", "Create"}
    assert len(service.operations) == 3
    assert service.shapes["test#Choice"].enum_values == ("one",)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("providerARNs", "provider_arns"),
        ("SAMLProviderARNs", "saml_provider_arns"),
        ("GetSMSAttributes", "get_sms_attributes"),
        ("CustomJWTAuthorizerConfiguration", "custom_jwt_authorizer_configuration"),
        ("MaxRecordSizeInKiB", "max_record_size_in_kib"),
        ("WarmThroughputMiBps", "warm_throughput_mibps"),
        ("ReadLimitMiBps", "read_limit_mibps"),
    ],
)
def test_rust_acronym_naming(name: str, expected: str) -> None:
    assert to_snake_case(name) == expected


@pytest.mark.parametrize(
    ("values", "valid"),
    [
        ([3, 6, 12], True),
        ([-(2**31), 2**31 - 1], True),
        ([], False),
        ([3, 3], False),
        ([True], False),
        (["3"], False),
        ([3.0], False),
        ([None], False),
        ([2**31], False),
        ([-(2**31) - 1], False),
    ],
)
def test_integer_enum_values(tmp_path: Path, values: list[object], valid: bool) -> None:
    model = {
        "smithy": "2.0",
        "shapes": {
            "test#Service": {"type": "service", "version": "1"},
            "test#Capacity": {
                "type": "intEnum",
                "members": {
                    f"VALUE_{index}": {
                        "target": "smithy.api#Unit",
                        "traits": {"smithy.api#enumValue": value},
                    }
                    for index, value in enumerate(values)
                },
            },
        },
    }
    raw = json.dumps(model).encode()
    path = tmp_path / "model.json"
    path.write_bytes(raw)
    descriptor = replace(get_service("sqs"), model_sha256=hashlib.sha256(raw).hexdigest())
    if not valid:
        with pytest.raises(ValueError, match="test#Capacity"):
            load_model(path, descriptor)
        return
    capacity = load_model(path, descriptor).shapes["test#Capacity"]
    assert capacity.kind == "intEnum"
    assert capacity.int_enum_values == tuple(values)
    assert capacity.members == ()
