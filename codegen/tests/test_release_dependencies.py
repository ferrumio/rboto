from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path
from types import ModuleType
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).parents[2]


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_dependencies", ROOT / ".github/scripts/release_dependencies.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(root: Path, name: str, dependencies: list[str]) -> Path:
    path = root / "packages" / name / "pyproject.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[project]\nname = "{name}"\nversion = "0.4.0"\n'
        f"dependencies = {json.dumps(dependencies)}\n",
    )
    return path


def wheel(root: Path, name: str, *, requires: str = "") -> Path:
    """A valid dependency wheel for exercising pip, without an index or native build."""
    path = root / f"{name}-1.0-py3-none-any.whl"
    metadata = f"{name}-1.0.dist-info"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            f"{metadata}/METADATA",
            f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\n{requires}",
        )
        archive.writestr(
            f"{metadata}/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            f"{metadata}/RECORD",
            "".join(f"{metadata}/{filename},,\n" for filename in ("METADATA", "WHEEL", "RECORD")),
        )
    return path


def test_requirements_follow_all_packages_and_preserve_markers_and_extras(tmp_path: Path) -> None:
    checker = load_script()
    manifest(tmp_path, "rboto", ["base-dependency>=1"])
    manifest(tmp_path, "rboto-s3", ["rboto==0.4.0", "typing-extensions>=4.15,<5"])
    manifest(tmp_path, "rboto-new-client", [
        "rboto==0.4.0",
        "typing-extensions>=4.15,<5",
        'future-dependency[feature]>=2; python_version >= "3.14" and sys_platform == "darwin"',
    ])
    requirements = tmp_path / "requirements.txt"
    checker.write_requirements(tmp_path, requirements)
    assert requirements.read_text().splitlines() == [
        "base-dependency>=1",
        'future-dependency[feature]>=2; python_version >= "3.14" and sys_platform == "darwin"',
        "typing-extensions<5,>=4.15",
    ]


def test_optional_telemetry_and_trio_are_not_required_by_all_services(tmp_path: Path) -> None:
    checker = load_script()
    core = manifest(tmp_path, "rboto", [])
    with core.open("a") as stream:
        stream.write(
            '\n[project.optional-dependencies]\n'
            'all = ["rboto-example==0.4.0"]\n'
            'opentelemetry = ["opentelemetry-api"]\ntrio = ["trio"]\n',
        )
    manifest(tmp_path, "rboto-example", ["rboto==0.4.0"])
    requirements = tmp_path / "requirements.txt"
    checker.write_requirements(tmp_path, requirements)
    assert requirements.read_text() == ""


@pytest.mark.parametrize(("dependency", "message"), [
    ("rboto==0.3.0", "disagrees with the release version"),
    ("rboto-missing==0.4.0", "missing release manifest"),
    ("rboto[trio]==0.4.0", "internal dependency extras"),
    ("external @ https://example.invalid/external.whl", "direct URLs"),
])
def test_unsupported_dependencies_fail_before_any_download(
    tmp_path: Path, dependency: str, message: str,
) -> None:
    checker = load_script()
    manifest(tmp_path, "rboto", [])
    manifest(tmp_path, "rboto-example", [dependency])
    output = tmp_path / "requirements.txt"
    with pytest.raises(ValueError, match=message):
        checker.write_requirements(tmp_path, output)
    assert not output.exists()


def test_dynamic_or_missing_manifests_do_not_pass_preflight(tmp_path: Path) -> None:
    checker = load_script()
    output = tmp_path / "requirements.txt"
    with pytest.raises(ValueError, match="facade and service manifests"):
        checker.write_requirements(tmp_path, output)
    manifest(tmp_path, "rboto", [])
    service = manifest(tmp_path, "rboto-example", [])
    with service.open("a") as stream:
        stream.write('dynamic = ["dependencies"]\n')
    with pytest.raises(ValueError, match="metadata must be static"):
        checker.write_requirements(tmp_path, output)


def test_offline_check_rejects_missing_dependency_then_accepts_complete_wheelhouse(
    tmp_path: Path,
) -> None:
    checker = load_script()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("release-fixture-parent==1.0\n")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    # A transitive dependency catches wheelhouses containing only the direct requirements.
    wheel(wheels, "release_fixture_parent", requires="Requires-Dist: release-fixture-child>=1\n")
    with pytest.raises(subprocess.CalledProcessError):
        checker.check(requirements, wheels)
    wheel(wheels, "release_fixture_child")
    checker.check(requirements, wheels)


def test_offline_resolver_rejects_conflicts_and_evaluates_markers(tmp_path: Path) -> None:
    checker = load_script()
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel(wheels, "release_fixture")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("release-fixture>=1\nrelease-fixture<1\n")
    with pytest.raises(subprocess.CalledProcessError):
        checker.check(requirements, wheels)
    requirements.write_text('release-fixture==1.0\nmissing-dependency; python_version < "3"\n')
    checker.check(requirements, wheels)


def test_empty_dependency_set_is_valid(tmp_path: Path) -> None:
    checker = load_script()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("")
    wheels = tmp_path / "wheels"
    checker.download(requirements, wheels)
    checker.check(requirements, wheels)
    assert list(wheels.iterdir()) == []


def test_third_party_wheels_cannot_be_published_as_release_packages(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "release_artifacts", ROOT / ".github/scripts/release_artifacts.py",
    )
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    catalog = tmp_path / "release.json"
    catalog.write_text(json.dumps({
        "version": "0.4.0", "services": [], "python": [], "platforms": [],
    }))
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel(dist, "typing_extensions")
    checksums = tmp_path / "SHA256SUMS"
    with pytest.raises(ValueError, match="unexpected distribution"):
        checker.validate(dist, catalog, checksums)
    assert not checksums.exists()


def workflow_job(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", source)
    assert match is not None, name
    return match.group(1)


def test_release_builds_wait_for_dependencies_and_smoke_consumes_separate_artifacts() -> None:
    release = (ROOT / ".github/workflows/release.yml").read_text()
    preflight = (ROOT / ".github/workflows/release-dependencies.yml").read_text()
    assert "uses: ./.github/workflows/release-dependencies.yml" in workflow_job(
        release, "dependencies",
    )
    for job in ("wheels", "service-sdist"):
        needs = re.search(r"needs: \[([^]]+)\]", workflow_job(release, job))
        assert needs is not None
        assert "dependencies" in {name.strip() for name in needs.group(1).split(",")}
    artifact = "name: release-dependencies--${{ matrix.platform.name }}"
    smoke = workflow_job(release, "smoke-wheelhouse")
    assert artifact in preflight and artifact in smoke
    assert '--find-links "dependencies/$ABI"' in smoke
    assert '--find-links "/io/dependencies/$ABI"' in smoke
    assert "--network none" in preflight and "--network none" in smoke
    for job in ("validate", "publish-services", "publish-core", "github-release"):
        assert "release-dependencies--" not in workflow_job(release, job)


def test_dependency_preflight_covers_the_complete_release_target_matrix() -> None:
    preflight = (ROOT / ".github/workflows/release-dependencies.yml").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    platform_names = r"\{ name: ([\w-]+),"
    assert set(re.findall(platform_names, preflight)) == set(re.findall(platform_names, release))
    python_loop = re.search(r"for version in ([\d. ]+);", preflight)
    abi_loop = re.search(r"for abi in ([cp\d ]+);", preflight)
    assert python_loop is not None and abi_loop is not None
    versions = set(re.findall(r'"(3\.\d+)"', workflow_job(release, "smoke-wheelhouse")))
    assert set(python_loop.group(1).split()) == versions
    assert set(abi_loop.group(1).split()) == {
        f"cp{version.replace('.', '')}" for version in versions
    }
