"""Shared package metadata derived from the service descriptors."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .registry import list_services


def generate_catalog(repository_root: Path) -> None:
    services = list_services()
    manifest = repository_root / "packages/rboto/pyproject.toml"
    text = manifest.read_text()
    version = str(tomllib.loads(text)["project"]["version"])
    extras = ["[project.optional-dependencies]"]
    for service in services:
        extras.append(f'{service.service_id} = ["{service.distribution_name}=={version}"]')
    extras.extend(
        ["all = ["]
        + [f'    "{service.distribution_name}=={version}",' for service in services]
        + ["]", "", ""]
    )
    updated, count = re.subn(
        r"(?ms)^\[project\.optional-dependencies\]\n.*?(?=^\[)",
        "\n".join(extras),
        text,
    )
    if count != 1:
        raise ValueError("expected one optional-dependencies section")
    manifest.write_text(updated)

    config_path = repository_root / "pyrightconfig.json"
    config = json.loads(config_path.read_text())
    config["include"] = [
        "packages/rboto/src",
        "packages/rboto-*/python",
        "codegen/src",
        "codegen/tests",
        "examples",
        "tests/typecheck",
    ]
    config["extraPaths"] = [
        "packages/rboto/src",
        "codegen/src",
        *(f"packages/{service.distribution_name}/python" for service in services),
    ]
    config_path.write_text(json.dumps(config, indent=2) + "\n")
