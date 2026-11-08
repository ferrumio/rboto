"""Every structure uses its exact Python field names, including future services."""

import re
from dataclasses import replace
from pathlib import Path

import pytest
from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import get_service, list_services
from rboto_codegen.rust_crate import RustField, RustType

ROOT = Path(__file__).parents[2]


class InputRenderer(ServiceGenerator):
    def structure(self, fields: tuple[RustField, ...]) -> str:
        return self._struct_from_py(RustType("Example", fields, (), False, False, True))


def test_future_service_uses_sorted_python_names_and_handles_empty_structures() -> None:
    generator = InputRenderer(
        replace(get_service("sqs"), service_id="future-service"),
        ROOT / "codegen/models/sqs.json", ROOT,
    )
    fields = tuple(
        RustField(name, rust_name, "::std::string::String", True)
        for name, rust_name in (("zebra", "zebra"), ("type", "r#type"), ("and", "and"))
    )
    source = generator.structure(fields)
    assert 'validate_input_fields(values, &["and", "type", "zebra"])?;' in source
    assert source.index("validate_input_fields") < source.index("::builder()")
    assert "validate_input_fields(values, &[])?;" in generator.structure(())


@pytest.mark.parametrize("service", list_services(), ids=lambda service: service.service_id)
def test_all_generated_structure_schemas_match_their_converted_fields(
    service: ServiceDescriptor,
) -> None:
    source = (ROOT / "crates" / service.distribution_name / "src/generated.rs").read_text()
    functions = re.findall(r"(?m)^fn \w+_from_py\([\s\S]*?^\}", source)
    structures = [function for function in functions if "::builder()" in function]
    assert structures
    for function in structures:
        schema = re.search(
            r"validate_input_fields\(\s*values,\s*&\[(.*?)\],?\s*\)\?", function, re.S,
        )
        assert schema is not None, function.splitlines()[0]
        names = re.findall(r'"([^"]+)"', schema[1])
        converted = re.findall(r'dict_value\(\s*values,\s*"([^"]+)"\s*,?\s*\)', function)
        assert names == sorted(converted), function.splitlines()[0]
