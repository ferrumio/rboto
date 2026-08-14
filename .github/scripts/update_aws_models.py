from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
import urllib.request
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = ROOT / "codegen/src/rboto_codegen/services"


def fetch(url: str) -> bytes:
    return cast(bytes, urllib.request.urlopen(url, timeout=120).read())


def latest_release() -> str:
    raw_tags = cast(
        list[object],
        json.loads(
            fetch("https://api.github.com/repos/awslabs/aws-sdk-rust/tags?per_page=100")
        ),
    )
    names: list[str] = []
    for raw_entry in raw_tags:
        entry = cast(dict[str, object], raw_entry)
        name = entry.get("name")
        if isinstance(name, str) and name.startswith("release-"):
            names.append(name)
    if not names:
        raise RuntimeError("no AWS SDK release tags found")
    return max(names)


def replace_toml_string(text: str, key: str, value: str) -> str:
    pattern = rf'^{re.escape(key)} = ".*"$'
    replacement = f'{key} = "{value}"'
    updated, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"expected exactly one {key!r} entry")
    return updated


def update_service(path: Path, release: str, versions: dict[str, object]) -> None:
    data = cast(dict[str, object], tomllib.loads(path.read_text()))
    crate_name = cast(str, data["rust_crate"])
    service_id = cast(str, data["service_id"])
    crates = cast(dict[str, object], versions["crates"])
    crate = cast(dict[str, object], crates[crate_name])
    crate_version = cast(str, crate["version"])
    model_hash = cast(str, crate["model_hash"])
    smithy_revision = cast(str, versions["smithy_rs_revision"])
    model_url = (
        "https://raw.githubusercontent.com/awslabs/aws-sdk-rust/"
        f"{release}/aws-models/{service_id}.json"
    )
    model_sha256 = hashlib.sha256(fetch(model_url)).hexdigest()

    text = path.read_text()
    for key, value in (
        ("rust_crate_version", crate_version),
        ("model_url", model_url),
        ("model_sha256", model_sha256),
        ("aws_model_hash", model_hash),
        ("smithy_codegen_revision", smithy_revision),
    ):
        text = replace_toml_string(text, key, value)
    path.write_text(text)


def update_workspace_dependencies(versions: dict[str, object]) -> None:
    crates = cast(dict[str, object], versions["crates"])
    path = ROOT / "Cargo.toml"
    text = path.read_text()
    for dependency in (
        "aws-config",
        "aws-smithy-async",
        "aws-smithy-runtime-api",
        "aws-smithy-types",
    ):
        metadata = cast(dict[str, object], crates[dependency])
        version = cast(str, metadata["version"])
        pattern = rf'^{re.escape(dependency)} = "=.*"$'
        replacement = f'{dependency} = "={version}"'
        text, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
        if count != 1:
            raise RuntimeError(f"workspace dependency not found: {dependency}")
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", default="latest")
    args = parser.parse_args()
    release = latest_release() if args.release == "latest" else args.release
    versions_url = (
        "https://raw.githubusercontent.com/awslabs/aws-sdk-rust/"
        f"{release}/versions.toml"
    )
    versions = cast(dict[str, object], tomllib.loads(fetch(versions_url).decode()))

    for path in sorted(SERVICE_DIR.glob("*.toml")):
        update_service(path, release, versions)
    update_workspace_dependencies(versions)
    print(release)


if __name__ == "__main__":
    main()
