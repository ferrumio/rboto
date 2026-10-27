import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from rboto_codegen.manifests import render_cargo
from rboto_codegen.registry import get_service, list_services, load_service, service_config_dir
from rboto_codegen.rust_crate import validate_crate_features


def test_service_library_targets_cannot_collide_in_a_shared_cargo_cache() -> None:
    targets = []
    for descriptor in list_services():
        manifest = tomllib.loads(render_cargo(descriptor))
        target = manifest["lib"].get("name", manifest["package"]["name"].replace("-", "_"))
        targets.append(target)
    assert len(targets) == len(set(targets))


@pytest.mark.parametrize(
    "value", ['"default"', '[]', '["rt-tokio", "rt-tokio"]', '[true]', '["a/b"]']
)
def test_descriptor_rejects_invalid_feature_lists(tmp_path: Path, value: str) -> None:
    path = tmp_path / "sns.toml"
    text = (service_config_dir() / path.name).read_text()
    text = text.replace(
        'rust_crate_features = ["default-https-client", "rt-tokio"]',
        f"rust_crate_features = {value}",
    )
    path.write_text(text)
    with pytest.raises(ValueError, match="rust_crate_features"):
        load_service(path)


@pytest.mark.parametrize("new_default", [False, True])
def test_model_updates_require_review_of_new_sdk_capabilities(
    tmp_path: Path, new_default: bool
) -> None:
    descriptor = get_service("sns")
    defaults = '"rustls", "default-https-client", "rt-tokio"'
    if new_default:
        defaults += ', "new-auth"'
    (tmp_path / "Cargo.toml").write_text(
        f"[features]\ndefault = [{defaults}]\n"
        'rustls = []\ndefault-https-client = []\nrt-tokio = []\nnew-auth = []\n'
    )
    if new_default:
        with pytest.raises(ValueError, match="review rust_crate_features.*new-auth"):
            validate_crate_features(descriptor, tmp_path)
        descriptor = replace(
            descriptor, rust_crate_features=(*descriptor.rust_crate_features, "new-auth")
        )
    validate_crate_features(descriptor, tmp_path)


def test_s3_signing_capability_cannot_be_silently_removed(tmp_path: Path) -> None:
    descriptor = get_service("s3")
    (tmp_path / "Cargo.toml").write_text(
        '[features]\ndefault = ["rustls", "sigv4a", "http-1x", '
        '"default-https-client", "rt-tokio"]\nrustls = []\nsigv4a = []\n'
        'http-1x = []\ndefault-https-client = []\nrt-tokio = []\n'
    )
    validate_crate_features(descriptor, tmp_path)
    descriptor = replace(
        descriptor,
        rust_crate_features=tuple(f for f in descriptor.rust_crate_features if f != "sigv4a"),
    )
    with pytest.raises(ValueError, match="sigv4a"):
        validate_crate_features(descriptor, tmp_path)
