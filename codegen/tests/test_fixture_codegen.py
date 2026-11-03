"""Fixture construction follows parsed native output contracts, including builders."""

from pathlib import Path

from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.registry import get_service

ROOT = Path(__file__).parents[2]


class FixtureRenderer(ServiceGenerator):
    def reachable_names(self) -> set[str]:
        return {item.name for item in self._fixture_types()}

    def factory(self, operation: str) -> str:
        metadata = next(
            item for item in self._native_output_operations() if item["operation"] == operation
        )
        return self._fixture_factory(metadata)


def test_parse_fallible_operation_builder_and_recursive_union_reachability() -> None:
    generator = FixtureRenderer(
        get_service("dynamodb"), ROOT / "codegen/models/dynamodb.json", ROOT,
    )
    assert generator.crate.operations["describe_endpoints"].output_build_fallible
    assert not generator.crate.operations["list_tables"].output_build_fallible
    assert "AttributeValue" in generator.reachable_names()
    assert "ConsumedCapacity" in generator.reachable_names()
    assert 'fixture_fields(values, &["endpoints"], &["endpoints"])' in generator.factory(
        "describe_endpoints"
    )
    assert "map_err" in generator.factory("describe_endpoints")
    assert "map_err" not in generator.factory("list_tables")
