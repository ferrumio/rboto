from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_first_version(path: Path, version: str) -> None:
    text = path.read_text()
    updated, count = re.subn(
        r'^version = "[^"]+"$',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError(f"version field not found: {path}")
    path.write_text(updated)


def update_extra_pins(path: Path, version: str) -> None:
    text = re.sub(r"(rboto-[a-z0-9-]+)==[0-9.]+", rf"\1=={version}", path.read_text())
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    args = parser.parse_args()
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.-]+)?", args.version) is None:
        raise SystemExit(f"invalid version: {args.version}")

    replace_first_version(ROOT / "Cargo.toml", args.version)
    replace_first_version(ROOT / "packages/rboto/pyproject.toml", args.version)
    replace_first_version(ROOT / "codegen/pyproject.toml", args.version)
    replace_first_version(
        ROOT / "codegen/src/rboto_codegen/templates/pyproject.toml.j2",
        args.version,
    )
    update_extra_pins(ROOT / "packages/rboto/pyproject.toml", args.version)
    print(args.version)


if __name__ == "__main__":
    main()
