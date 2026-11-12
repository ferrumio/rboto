"""Snapshot existing installed bindings for grading; never invokes a Rust build."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .runner import Json

BASE = "python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704"
MODULES = ("rboto", "rboto_s3", "rboto_dynamodb", "rboto_sqs", "rboto_bedrock_runtime")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot(root: Path, destination: Path, version: str) -> Json:
    if platform.python_version() != "3.12.15" or sys.platform != "linux":
        raise ValueError("snapshot requires installed Linux CPython 3.12.15 bindings")
    destination.mkdir(parents=True, exist_ok=False)
    manifest: Json = {
        "base": BASE,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sdk_version": version,
        "sdk_source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
        ).strip(),
        "sdk_source_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--", "packages", "crates", "codegen"],
                cwd=root,
                text=True,
            ).strip()
        ),
        "files": {},
        "native_source_files": {},
        "provenance": "snapshot of existing installed bindings; source revision alone "
        "does not attest native compilation provenance",
    }
    for module in MODULES:
        spec = importlib.util.find_spec(module)
        if spec is None or spec.origin is None:
            raise ValueError(f"install {module} before preparing the image")
        installed_version = importlib.metadata.version(module.replace("_", "-"))
        if installed_version != version:
            raise ValueError(f"{module} version is {installed_version}, expected {version}")
        source = Path(spec.origin).parent
        target = destination / "sdk" / module
        target.mkdir(parents=True)
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(source)
            copied = target / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".so":
                manifest["native_source_files"][f"{module}/{relative}"] = digest(path)
                subprocess.run(
                    ["strip", "--strip-debug", "-o", str(copied), str(path)],
                    check=True,
                )
            else:
                shutil.copyfile(path, copied)
            manifest["files"][f"{module}/{relative}"] = digest(copied)
    shutil.copytree(
        root / "evals/rboto_evals",
        destination / "rboto_evals",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copyfile(root / "evals/requirements.lock", destination / "requirements.lock")
    manifest["requirements_sha256"] = digest(destination / "requirements.lock")
    manifest["grader_files"] = {
        str(path.relative_to(destination)): digest(path)
        for path in sorted((destination / "rboto_evals").rglob("*.py"))
    }
    (destination / "Dockerfile").write_text(
        f"FROM {BASE}\n"
        "COPY requirements.lock /opt/requirements.lock\n"
        "RUN pip install --no-cache-dir --require-hashes -r /opt/requirements.lock\n"
        "COPY sdk /opt/sdk\n"
        "COPY rboto_evals /opt/evals/rboto_evals\n"
        'ENV PYTHONPATH="/opt/sdk:/opt/evals" PYTHONDONTWRITEBYTECODE=1 '
        "AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing "
        "AWS_EC2_METADATA_DISABLED=true AWS_MAX_ATTEMPTS=1\n"
        "WORKDIR /tmp\n"
    )
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def prepare(root: Path, destination: Path, version: str, command: list[str], tag: str) -> Json:
    manifest = snapshot(root, destination, version)
    subprocess.run([*command, "build", "-t", tag, str(destination)], check=True)
    manifest["image_id"] = subprocess.check_output(
        [*command, "image", "inspect", "--format", "{{.Id}}", tag],
        text=True,
    ).strip()
    manifest["installed_dependencies"] = subprocess.check_output(
        [
            *command,
            "run",
            "--rm",
            "--network",
            "none",
            manifest["image_id"],
            "python",
            "-m",
            "pip",
            "freeze",
        ],
        text=True,
    ).splitlines()
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
