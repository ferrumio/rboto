"""Validate a complete wheelhouse and resume uploads only for identical artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import time
from email.parser import BytesParser
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zipfile import ZipFile

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def platform_family(platform: str) -> str:
    for arch in ("x86_64", "aarch64"):
        if platform in (f"manylinux_2_17_{arch}", f"manylinux2014_{arch}"):
            return f"manylinux-{arch}"
        if platform == f"musllinux_1_2_{arch}":
            return f"musllinux-{arch}"
    for arch in ("x86_64", "arm64"):
        if platform.startswith("macosx_") and platform.endswith(f"_{arch}"):
            return f"macos-{arch}"
    raise ValueError(f"unsupported wheel platform: {platform}")


def artifact_identity(path: Path) -> tuple[str, str]:
    if path.suffix == ".whl":
        name, version, _, _ = parse_wheel_filename(path.name)
    else:
        name, version = parse_sdist_filename(path.name)
    return str(name), str(version)


def validate(dist: Path, catalog_path: Path, checksums: Path) -> None:
    catalog = json.loads(catalog_path.read_text())
    services = cast(list[dict[str, str]], catalog["services"])
    packages = {service["distribution"]: service["module"] for service in services}
    packages["rboto"] = "rboto"
    expected = {("rboto", "py3", "any"), *(
        (service["distribution"], f"cp{python}", platform)
        for service in services
        for python in catalog["python"]
        for platform in catalog["platforms"]
    )}
    wheels: set[tuple[str, str, str]] = set()
    sources: set[str] = set()
    files = sorted(path for path in dist.iterdir() if path.is_file())
    if not files:
        raise ValueError("empty wheelhouse")
    for path in files:
        name, version = artifact_identity(path)
        if name not in packages or version != catalog["version"]:
            raise ValueError(f"unexpected distribution or version: {path.name}")
        if path.suffix == ".whl":
            _, _, build, tags = parse_wheel_filename(path.name)
            if build:
                raise ValueError(f"unexpected wheel build tag: {path.name}")
            identities = set()
            for tag in tags:
                if name == "rboto":
                    if (tag.interpreter, tag.abi, tag.platform) != ("py3", "none", "any"):
                        raise ValueError(f"invalid facade wheel: {path.name}")
                    identities.add((name, "py3", "any"))
                else:
                    if tag.abi != tag.interpreter or not tag.abi.startswith("cp"):
                        raise ValueError(f"expected a per-CPython ABI: {path.name}")
                    identities.add((name, tag.interpreter, platform_family(tag.platform)))
            if len(identities) != 1 or identities & wheels:
                raise ValueError(f"duplicate or ambiguous wheel: {path.name}")
            wheels.update(identities)
            with ZipFile(path) as archive:
                names = archive.namelist()
                metadata_files = [item for item in names if item.endswith(".dist-info/METADATA")]
                if len(metadata_files) != 1:
                    raise ValueError(f"invalid wheel metadata: {path.name}")
                metadata = archive.read(metadata_files[0])
                required = {f"{packages[name]}/py.typed"}
                if name != "rboto":
                    required.add(f"{packages[name]}/_native.pyi")
                if not required.issubset(names):
                    raise ValueError(f"missing typing files: {path.name}")
        else:
            if name in sources:
                raise ValueError(f"duplicate source distribution: {path.name}")
            sources.add(name)
            with tarfile.open(path) as archive:
                members = [member for member in archive.getmembers()
                           if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO")]
                if len(members) != 1:
                    raise ValueError(f"invalid source metadata: {path.name}")
                stream = archive.extractfile(members[0])
                if stream is None:
                    raise ValueError(f"missing source metadata: {path.name}")
                metadata = stream.read()
        parsed = BytesParser().parsebytes(metadata)
        if canonicalize_name(str(parsed["Name"])) != name or parsed["Version"] != version:
            raise ValueError(f"metadata disagrees with filename: {path.name}")
    if wheels != expected or sources != set(packages):
        raise ValueError(
            f"incomplete wheelhouse: missing wheels={sorted(expected - wheels)}, "
            f"unexpected wheels={sorted(wheels - expected)}, "
            f"missing sdists={sorted(set(packages) - sources)}"
        )
    checksums.parent.mkdir(parents=True, exist_ok=True)
    checksums.write_text("".join(f"{digest(path)}  {path.name}\n" for path in files))
    print(f"Validated {len(wheels)} wheels and {len(sources)} source distributions.")


def published_files(name: str, version: str) -> dict[str, str]:
    for attempt in range(3):
        try:
            with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
                data = json.load(response)
            return {item["filename"]: item["digests"]["sha256"] for item in data["urls"]}
        except HTTPError as error:
            if error.code == 404:
                return {}
            if error.code < 500 and error.code != 429:
                raise
            if attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def prepare_upload(dist: Path, checksums: Path, output: Path | None) -> int:
    hashes = {filename: sha for sha, filename in (
        line.split("  ", 1) for line in checksums.read_text().splitlines()
    )}
    files = sorted(path for path in dist.iterdir() if path.is_file())
    if not files:
        raise ValueError("no distributions to publish")
    identities = {artifact_identity(path) for path in files}
    if len(identities) != 1:
        raise ValueError("publish exactly one project and version per job")
    name, version = identities.pop()
    expected_files = {
        filename for filename in hashes if artifact_identity(Path(filename)) == (name, version)
    }
    if {path.name for path in files} != expected_files:
        raise ValueError(f"incomplete artifact download for {name}")
    # Verify all local files before querying PyPI or creating an upload directory.
    for path in files:
        if digest(path) != hashes.get(path.name):
            raise ValueError(f"artifact changed after validation: {path.name}")
    existing = published_files(name, version)
    pending = []
    for path in files:
        remote = existing.get(path.name)
        if remote is not None and remote != hashes[path.name]:
            raise ValueError(f"PyPI already has different bytes for {path.name}")
        if remote is None:
            pending.append(path)
    if output is None:
        if pending:
            raise ValueError(f"publication is incomplete: {[path.name for path in pending]}")
    elif pending:
        output.mkdir(parents=True, exist_ok=False)
        for path in pending:
            shutil.copyfile(path, output / path.name)
    print(f"{name} {version}: {len(files) - len(pending)} identical files on PyPI; "
          f"{len(pending)} remaining.")
    return len(pending)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "prepare-upload", "verify-published"))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--catalog", type=Path, default=Path("release/release.json"))
    parser.add_argument("--checksums", type=Path, default=Path("release/SHA256SUMS"))
    parser.add_argument("--output", type=Path, default=Path("upload"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        validate(args.dist, args.catalog, args.checksums)
    else:
        pending = prepare_upload(
            args.dist, args.checksums,
            args.output if args.command == "prepare-upload" else None,
        )
        if args.github_output is not None:
            with args.github_output.open("a") as stream:
                stream.write(f"pending={'true' if pending else 'false'}\n")


if __name__ == "__main__":
    main()
