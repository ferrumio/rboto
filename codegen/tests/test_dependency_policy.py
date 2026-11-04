"""SDK updates must preserve client capabilities without restoring legacy TLS."""

import tomllib
from pathlib import Path

import pytest
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import list_services, load_service
from rboto_codegen.rust_crate import find_crate

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("descriptor", list_services(), ids=lambda item: item.service_id)
def test_sdk_features_preserve_defaults_except_legacy_tls(descriptor: ServiceDescriptor) -> None:
    upstream = tomllib.loads((find_crate(descriptor) / "Cargo.toml").read_text())
    manifest = ROOT / "crates" / descriptor.distribution_name / "Cargo.toml"
    dependency = tomllib.loads(manifest.read_text())["dependencies"][descriptor.rust_crate]
    assert dependency["default-features"] is False
    assert set(dependency["features"]) == set(upstream["features"]["default"]) - {"rustls"}
    assert {"default-https-client", "rt-tokio"} <= set(dependency["features"])


def test_shared_sdk_versions_are_centralized_for_the_updater() -> None:
    workspace = tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"]["dependencies"]
    for path in (ROOT / "crates").glob("*/Cargo.toml"):
        for name, dependency in tomllib.loads(path.read_text())["dependencies"].items():
            if name.startswith("aws-") and not name.startswith("aws-sdk-"):
                assert dependency.get("workspace") is True, (path, name)
                assert workspace[name].startswith("="), name
                assert "version" not in dependency, (path, name)


def test_lockfile_excludes_tls_versions_affected_by_open_advisories() -> None:
    # GHSA-w44r-93j7-qv28, GHSA-c8m5-c952-6cm4, GHSA-h2v8-394m-p7vv;
    # GHSA-82j2-j2ch-gfr8, GHSA-xgp8-3hg3-c2mh, GHSA-965h-392x-2mh5.
    minimums = {"rustls": (0, 23, 46), "rustls-webpki": (0, 103, 13)}
    packages = tomllib.loads((ROOT / "Cargo.lock").read_text())["package"]
    for name, minimum in minimums.items():
        versions = [tuple(map(int, item["version"].split(".")))
                    for item in packages if item["name"] == name]
        assert versions
        assert all(version >= minimum for version in versions), (name, versions)


@pytest.mark.parametrize("features", [
    '"sigv4a"', '[1]', '[""]', '["sigv4a", "sigv4a"]', '["rustls"]', '["default"]',
])
def test_invalid_or_legacy_sdk_features_are_rejected(tmp_path: Path, features: str) -> None:
    path = tmp_path / "kms.toml"
    original = ROOT / "codegen/src/rboto_codegen/services/kms.toml"
    path.write_text(f"rust_features = {features}\n" + original.read_text())
    with pytest.raises(ValueError, match="rust_features"):
        load_service(path)
