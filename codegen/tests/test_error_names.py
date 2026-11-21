import ast
from pathlib import Path

import pytest
from rboto_codegen.error_names import error_class_names
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import list_services

ROOT = Path(__file__).parents[2]


def test_modeled_errors_keep_distinct_deterministic_names() -> None:
    names = ["ResourceNotFoundException", "ResourceNotFound"]
    expected = {
        "ResourceNotFound": "ResourceNotFoundError",
        "ResourceNotFoundException": "ResourceNotFoundExceptionError",
    }
    assert error_class_names(names, "ServiceError") == expected
    assert error_class_names(reversed(names), "ServiceError") == expected


def test_collision_alternatives_reserve_existing_names_and_service_base() -> None:
    names = ["Service", "Thing", "ThingException", "ThingExceptionError", "BaseService"]
    allocated = error_class_names(names, "ServiceError")
    assert len(set(allocated.values())) == len(names)
    assert "ServiceError" not in allocated.values()
    assert "BaseServiceError" not in allocated.values()
    assert allocated["Thing"] == "ThingError"
    assert allocated["ThingExceptionError"] == "ThingExceptionError"


@pytest.mark.parametrize("descriptor", list_services(), ids=lambda item: item.service_id)
def test_exception_classes_do_not_shadow_other_modeled_errors(
    descriptor: ServiceDescriptor,
) -> None:
    source = ROOT / "packages" / descriptor.distribution_name / "python"
    tree = ast.parse((source / descriptor.python_package / "exceptions.py").read_text())
    names = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert len(names) == len(set(names))
