"""Render package manifests before their pinned Rust crates are downloaded."""

from __future__ import annotations

import tomllib
from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .model import ServiceDescriptor


def template_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def render_cargo(descriptor: ServiceDescriptor) -> str:
    return template_environment().get_template("Cargo.toml.j2").render(asdict(descriptor))


def render_pyproject(descriptor: ServiceDescriptor, repository_root: Path) -> str:
    workspace = tomllib.loads((repository_root / "Cargo.toml").read_text())
    version = workspace["workspace"]["package"]["version"]
    return template_environment().get_template("pyproject.toml.j2").render(
        **asdict(descriptor), version=version, public_name=descriptor.public_name,
    )


def generate_manifests(
    descriptor: ServiceDescriptor, repository_root: Path
) -> tuple[Path, Path]:
    cargo = repository_root / "crates" / descriptor.distribution_name / "Cargo.toml"
    pyproject = repository_root / "packages" / descriptor.distribution_name / "pyproject.toml"
    for path, content in (
        (cargo, render_cargo(descriptor)),
        (pyproject, render_pyproject(descriptor, repository_root)),
        (pyproject.parent / "LICENSE", (repository_root / "LICENSE").read_text()),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
    return cargo, pyproject
