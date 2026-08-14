from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]


def project_version(path: Path) -> str:
    data = cast(dict[str, object], tomllib.loads(path.read_text()))
    project = cast(dict[str, object], data["project"])
    version = project["version"]
    if not isinstance(version, str):
        raise TypeError(f"invalid project version in {path}")
    return version


def workspace_version() -> str:
    data = cast(dict[str, object], tomllib.loads((ROOT / "Cargo.toml").read_text()))
    workspace = cast(dict[str, object], data["workspace"])
    package = cast(dict[str, object], workspace["package"])
    version = package["version"]
    if not isinstance(version, str):
        raise TypeError("invalid Cargo workspace version")
    return version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected")
    args = parser.parse_args()

    versions = {"Cargo workspace": workspace_version()}
    for path in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        versions[str(path.relative_to(ROOT))] = project_version(path)
    versions["codegen/pyproject.toml"] = project_version(
        ROOT / "codegen/pyproject.toml"
    )

    unique = set(versions.values())
    if len(unique) != 1:
        details = "\n".join(f"{name}: {version}" for name, version in versions.items())
        raise SystemExit(f"versions are not synchronized:\n{details}")

    version = unique.pop()
    if args.expected is not None and version != args.expected:
        raise SystemExit(f"expected version {args.expected}, manifests contain {version}")
    print(version)


if __name__ == "__main__":
    main()
