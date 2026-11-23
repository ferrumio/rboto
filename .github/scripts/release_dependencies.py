"""Prepare third-party wheels for offline release installation, before compiling Rust."""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path


def write_requirements(root: Path, output: Path) -> None:
    # Only metadata discovery needs packaging; download/check run with the
    # unmodified interpreters supplied by the release platform images.
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    paths = sorted((root / "packages").glob("rboto*/pyproject.toml"))
    projects = {
        canonicalize_name(project["name"]): project
        for path in paths
        for project in [tomllib.loads(path.read_text())["project"]]
    }
    if "rboto" not in projects or len(projects) < 2:
        raise ValueError("release dependencies require the facade and service manifests")
    requirements: set[str] = set()
    for name, project in projects.items():
        if project.get("dynamic"):
            raise ValueError(f"{name}: release dependency metadata must be static")
        for value in project.get("dependencies", []):
            requirement = Requirement(value)
            dependency = canonicalize_name(requirement.name)
            if requirement.url:
                raise ValueError(f"{name}: direct URLs are incompatible with offline installation")
            if dependency in projects:
                if requirement.extras:
                    raise ValueError(f"{name}: internal dependency extras need release support")
                if projects[dependency]["version"] not in requirement.specifier:
                    raise ValueError(f"{name}: {requirement} disagrees with the release version")
                continue
            if dependency.startswith("rboto-"):
                raise ValueError(f"{name}: missing release manifest for {dependency}")
            requirements.add(str(requirement))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(f"{requirement}\n" for requirement in sorted(requirements)))
    print(f"Collected {len(requirements)} external requirements from {len(projects)} packages.")


def download(requirements: Path, wheels: Path) -> None:
    wheels.mkdir(parents=True, exist_ok=True)
    if requirements.read_text().strip():
        subprocess.run([
            sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check",
            "download", "--only-binary=:all:", "--dest", str(wheels),
            "--requirement", str(requirements),
        ], check=True)


def check(requirements: Path, wheels: Path) -> None:
    """Exercise pip's actual dependency resolver in an empty, offline environment."""
    with tempfile.TemporaryDirectory(prefix="rboto-release-dependencies-") as temporary:
        environment = Path(temporary) / "venv"
        venv.create(environment, with_pip=True)
        python = environment / "bin/python"
        if requirements.read_text().strip():
            subprocess.run([
                str(python), "-m", "pip", "--isolated", "--disable-pip-version-check",
                "install", "--no-cache-dir", "--only-binary=:all:", "--no-index",
                "--find-links", str(wheels.resolve()), "--requirement", str(requirements),
            ], check=True)
        subprocess.run([str(python), "-m", "pip", "check"], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["requirements", "download", "check"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--requirements", type=Path, default=Path("dependencies/requirements.txt"))
    parser.add_argument("--wheels", type=Path)
    args = parser.parse_args()
    if args.command == "requirements":
        write_requirements(args.root, args.requirements)
    elif args.wheels is None:
        parser.error("--wheels is required for download/check")
    elif args.command == "download":
        download(args.requirements, args.wheels)
    else:
        check(args.requirements, args.wheels)


if __name__ == "__main__":
    main()
