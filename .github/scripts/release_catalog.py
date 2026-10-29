"""Freeze the service catalog used to build, test, and publish one release."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import list_services

ROOT = Path(__file__).resolve().parents[2]


class Batch(TypedDict):
    name: str
    services: list[str]


def service_batches(services: Sequence[ServiceDescriptor]) -> list[Batch]:
    """Balance generated source size while keeping at most six services per job."""
    if not services:
        raise ValueError("release catalog contains no services")
    count = math.ceil(len(services) / 6)
    small, extra = divmod(len(services), count)
    capacities = [small + (index < extra) for index in range(count)]
    batches: list[Batch] = [
        {"name": f"batch-{index + 1}", "services": []} for index in range(count)
    ]
    weights = [0] * count
    sized = [
        (service.service_id, (ROOT / "crates" / service.distribution_name
                             / "src/generated.rs").stat().st_size)
        for service in services
    ]
    for service, size in sorted(sized, key=lambda item: (-item[1], item[0])):
        index = min(
            (i for i in range(count) if len(batches[i]["services"]) < capacities[i]),
            key=lambda i: (weights[i], i),
        )
        batches[index]["services"].append(service)
        weights[index] += size
    return batches


def write_catalog(version: str, commit: str, output: Path) -> None:
    services = list_services()
    output.mkdir(parents=True, exist_ok=True)
    catalog = {
        "version": version,
        "commit": commit,
        "batches": service_batches(services),
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
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    write_catalog(args.version, args.commit, args.output)
    if args.github_output is not None:
        catalog = json.loads((args.output / "release.json").read_text())
        with args.github_output.open("a") as stream:
            stream.write(f"batches={json.dumps(catalog['batches'])}\n")


if __name__ == "__main__":
    main()
