import json
from pathlib import Path
from typing import cast

import pytest
from rboto_codegen.cli_generator import CliServiceGenerator, generate_cli_manifest
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import list_services
from rboto_codegen.rust_crate import parse_crate

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "descriptor", list_services(), ids=[service.service_id for service in list_services()]
)
def test_cli_catalog_covers_every_pinned_sdk_operation(descriptor: ServiceDescriptor) -> None:
    catalog = cast(
        list[dict[str, object]], json.loads((ROOT / "cli/app/src/catalog.json").read_text())
    )
    row = next(row for row in catalog if row["service"] == descriptor.service_id)
    operations = cast(list[dict[str, object]], row["operations"])
    crate = parse_crate(descriptor)
    assert {row["name"] for row in operations} == set(crate.operations)
    for operation in operations:
        name = cast(str, operation["name"])
        parameters = cast(list[dict[str, object]], operation["parameters"])
        assert {field["name"] for field in parameters} == {
            field.name for field in crate.operations[name].input_fields
        }


def test_cli_manifest_bootstrap_needs_no_downloaded_crates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CARGO_HOME", str(tmp_path / "empty-cargo"))
    for descriptor in list_services():
        path = generate_cli_manifest(descriptor, tmp_path)
        assert f'version = "={descriptor.rust_crate_version}"' in path.read_text()


def test_cli_generation_is_stable_and_preserves_sdk_adapters(tmp_path: Path) -> None:
    descriptor = next(service for service in list_services() if service.service_id == "sns")
    generator = CliServiceGenerator(
        descriptor,
        ROOT / "codegen/models/sns.json",
        tmp_path,
    )
    paths = generator.generate_cli_service()
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    generator.generate_cli_service()
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
    assert not (tmp_path / "crates").exists()
    assert not (tmp_path / "packages").exists()
