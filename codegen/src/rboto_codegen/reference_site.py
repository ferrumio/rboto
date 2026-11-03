"""Generate operation references only when building the documentation website."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .generator import ServiceGenerator
from .reference import package_version, write_reference
from .registry import list_services


def check_links(root: Path) -> None:
    for path in sorted(root.rglob("*.md")):
        # Code examples and signatures aren't Markdown link sources.
        text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
        links: list[str] = re.findall(r"\]\(([^)]+)\)", text)
        for link in links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError(f"Broken reference link: {path}: {link}")
            if url.fragment:
                headings = {
                    re.sub(r"[^\w-]", "", heading.lower().replace(" ", "-"))
                    for heading in re.findall(r"^#+ (.+)$", target.read_text(), re.M)
                }
                if url.fragment not in headings:
                    raise ValueError(f"Broken reference anchor: {path}: {link}")


def prepare(root: Path) -> None:
    version = package_version(root)
    destination = root / "docs/reference"
    # This directory is ignored and reserved for staging, never handwritten docs.
    if destination.exists():
        shutil.rmtree(destination)
    index = [
        "# Service references\n",
        f"Source version: `{version}`. "
        "This website can be ahead of your installed package.\n",
    ]
    waiters = [
        "# Waiter catalog\n",
        f"Source version: `{version}`. Only validated definitions are exposed. "
        "This website can be ahead of your installed package.\n",
        "| Client | Waiter | Polling operation | Default delay |",
        "|---|---|---|---|",
    ]
    for descriptor in list_services():
        generator = ServiceGenerator(
            descriptor, root / "codegen/models" / f"{descriptor.service_id}.json", root,
        )
        files = generator.render_reference()
        if len({name.casefold() for name in files}) != len(files):
            raise ValueError(f"Case-insensitive reference path collision: {descriptor.service_id}")
        write_reference(destination / descriptor.service_id, files)
        index.append(f"- [{descriptor.public_name}]({descriptor.service_id}/index.md)")
        waiters.extend(
            f"| `{descriptor.client_class}` | "
            f"[`{item.name}`]({descriptor.service_id}/operations/{item.operation}.md#waiters) | "
            f"`{item.operation}` | {item.delay} s |"
            for item in generator.waiters
        )
    (destination / "services.md").write_text("\n".join(index) + "\n")
    (destination / "waiters.md").write_text("\n".join(waiters) + "\n")
    check_links(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[3])
    args = parser.parse_args()
    prepare(args.root)


if __name__ == "__main__":
    main()
