"""Freeze the service catalog used to build, test, and publish one release."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from rboto_codegen.registry import list_services

ROOT = Path(__file__).resolve().parents[2]


def write_catalog(version: str, commit: str, output: Path) -> None:
    services = list_services()
    output.mkdir(parents=True, exist_ok=True)
    catalog = {
        "version": version,
        "commit": commit,
        "python": ["312", "313", "314", "315"],
        "platforms": [
            "manylinux-x86_64", "manylinux-aarch64",
            "musllinux-x86_64", "musllinux-aarch64",
            "macos-x86_64", "macos-arm64",
        ],
        "services": [
            {
                "service": service.service_id,
                "distribution": service.distribution_name,
                "module": service.python_package,
                "client": service.client_class,
            }
            for service in services
        ],
    }
    (output / "release.json").write_text(json.dumps(catalog, indent=2) + "\n")
    with (output / "trusted-publishers.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["project", "owner", "repository", "workflow", "environment"])
        for name in ["rboto", *(service.distribution_name for service in services)]:
            writer.writerow([name, "ferrumio", "rboto", "release.yml", "pypi"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, default=Path("release"))
    args = parser.parse_args()
    write_catalog(args.version, args.commit, args.output)


if __name__ == "__main__":
    main()
