from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
from email.message import Message
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.error import HTTPError
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).parents[2]


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_wheel(root: Path, name: str, tag: str, *, typed: bool = True) -> Path:
    path = root / f"{name}-0.1.0-{tag}.whl"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name}-0.1.0.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {name}\nVersion: 0.1.0\n",
        )
        if typed:
            archive.writestr(f"{name}/py.typed", "")
            archive.writestr(f"{name}/_native.pyi", "")
    return path


def seed_wheelhouse(root: Path) -> tuple[Path, Path, Path]:
    dist = root / "dist"
    dist.mkdir()
    catalog = root / "release.json"
    catalog.write_text(json.dumps({
        "version": "0.1.0",
        "python": ["312", "313", "314", "315"],
        "platforms": [
            "manylinux-x86_64", "manylinux-aarch64",
            "musllinux-x86_64", "musllinux-aarch64", "macos-x86_64", "macos-arm64",
        ],
        "services": [
            {"service": "sts", "distribution": "rboto-sts", "module": "rboto_sts",
             "client": "STSClient"},
        ],
    }))
    for python in ("312", "313", "314", "315"):
        for platform in (
            "manylinux_2_17_x86_64.manylinux2014_x86_64",
            "manylinux_2_17_aarch64.manylinux2014_aarch64",
            "musllinux_1_2_x86_64", "musllinux_1_2_aarch64",
            "macosx_10_12_x86_64", "macosx_11_0_arm64",
        ):
            make_wheel(dist, "rboto_sts", f"cp{python}-cp{python}-{platform}")
    make_wheel(dist, "rboto", "py3-none-any")
    for name in ("rboto", "rboto_sts"):
        with tarfile.open(dist / f"{name}-0.1.0.tar.gz", "w:gz") as archive:
            metadata = f"Metadata-Version: 2.4\nName: {name}\nVersion: 0.1.0\n".encode()
            info = tarfile.TarInfo(f"{name}-0.1.0/PKG-INFO")
            info.size = len(metadata)
            archive.addfile(info, io.BytesIO(metadata))
    return dist, catalog, root / "SHA256SUMS"


def test_complete_release_requires_all_python_and_platform_wheels(tmp_path: Path) -> None:
    checker = load_script("release_artifacts")
    dist, catalog, checksums = seed_wheelhouse(tmp_path)
    checker.validate(dist, catalog, checksums)
    lines = checksums.read_text().splitlines()
    assert len(lines) == 27
    for line in lines:
        sha, filename = line.split("  ")
        assert hashlib.sha256((dist / filename).read_bytes()).hexdigest() == sha
    next(dist.glob("*cp315*musllinux*aarch64.whl")).unlink()
    with pytest.raises(ValueError, match="incomplete wheelhouse"):
        checker.validate(dist, catalog, checksums)


@pytest.mark.parametrize("problem", ["typing", "abi3", "sdist", "version", "metadata"])
def test_invalid_release_artifacts_block_publication(tmp_path: Path, problem: str) -> None:
    checker = load_script("release_artifacts")
    dist, catalog, checksums = seed_wheelhouse(tmp_path)
    wheel = next(dist.glob("*cp312*musllinux*x86_64.whl"))
    if problem == "typing":
        make_wheel(dist, "rboto_sts", "cp312-cp312-musllinux_1_2_x86_64", typed=False)
    elif problem == "abi3":
        wheel.rename(wheel.with_name(wheel.name.replace("cp312-cp312", "cp312-abi3")))
    elif problem == "sdist":
        (dist / "rboto_sts-0.1.0.tar.gz").unlink()
    elif problem == "version":
        wheel.rename(wheel.with_name(wheel.name.replace("0.1.0", "0.2.0")))
    else:
        with ZipFile(wheel, "w") as archive:
            archive.writestr("rboto_sts-0.1.0.dist-info/METADATA", "Name: wrong\nVersion: 0.1.0\n")
            archive.writestr("rboto_sts/py.typed", "")
            archive.writestr("rboto_sts/_native.pyi", "")
    with pytest.raises(ValueError):
        checker.validate(dist, catalog, checksums)
    assert not checksums.exists()


def test_upload_resumes_only_identical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = load_script("release_artifacts")
    dist, catalog, checksums = seed_wheelhouse(tmp_path)
    checker.validate(dist, catalog, checksums)
    # The publishing matrix downloads one project's complete artifact set.
    for path in dist.glob("rboto_sts*"):
        path.unlink()
    wheel = next(dist.glob("*.whl"))
    remote: dict[str, str] = {wheel.name: checker.digest(wheel)}

    def published_files(name: str, version: str) -> dict[str, str]:
        return remote

    monkeypatch.setattr(checker, "published_files", published_files)
    output = tmp_path / "upload"
    assert checker.prepare_upload(dist, checksums, output) == 1
    assert [path.name for path in output.iterdir()] == ["rboto-0.1.0.tar.gz"]
    with pytest.raises(ValueError, match="publication is incomplete"):
        checker.prepare_upload(dist, checksums, None)
    sdist = next(dist.glob("*.tar.gz"))
    remote[sdist.name] = checker.digest(sdist)
    assert checker.prepare_upload(dist, checksums, None) == 0
    assert checker.prepare_upload(dist, checksums, tmp_path / "retry") == 0
    assert not (tmp_path / "retry").exists()
    remote[wheel.name] = "0" * 64
    with pytest.raises(ValueError, match="different bytes"):
        checker.prepare_upload(dist, checksums, tmp_path / "conflict")
    assert not (tmp_path / "conflict").exists()


@pytest.mark.parametrize("changed", [True, False])
def test_missing_or_changed_local_files_fail_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool,
) -> None:
    checker = load_script("release_artifacts")
    dist, catalog, checksums = seed_wheelhouse(tmp_path)
    checker.validate(dist, catalog, checksums)
    for path in dist.glob("rboto_sts*"):
        path.unlink()
    wheel = next(dist.glob("*.whl"))
    if changed:
        wheel.write_bytes(b"different artifact")
    else:
        wheel.unlink()

    def unexpected_network(*args: object) -> None:
        pytest.fail("invalid local artifacts must not query PyPI")

    monkeypatch.setattr(checker, "published_files", unexpected_network)
    with pytest.raises(ValueError, match="changed|incomplete"):
        checker.prepare_upload(dist, checksums, tmp_path / "upload")


@pytest.mark.parametrize("status", [404, 403, 500])
def test_pypi_errors_are_not_silently_treated_as_missing_files(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    checker = load_script("release_artifacts")
    attempts: list[str] = []

    def request(url: str, **kwargs: object) -> None:
        attempts.append(url)
        raise HTTPError(url, status, "error", Message(), None)

    def sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(checker, "urlopen", request)
    monkeypatch.setattr(checker.time, "sleep", sleep)
    if status == 404:
        assert checker.published_files("rboto", "0.1.0") == {}
    else:
        with pytest.raises(HTTPError):
            checker.published_files("rboto", "0.1.0")
    assert len(attempts) == (3 if status == 500 else 1)


def test_smoke_uses_catalog_clients_instead_of_all_public_symbols(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = load_script("smoke_release")
    _, catalog, _ = seed_wheelhouse(tmp_path)
    created: list[tuple[str, str]] = []

    class STSClient:
        def __init__(self, *, region: str, endpoint_url: str) -> None:
            created.append((region, endpoint_url))
            self._native = SimpleNamespace(get_caller_identity=lambda: None)

        async def get_caller_identity(self) -> None:
            pass

    def invalid_helper() -> None:
        pytest.fail("release smoke must not call unrelated public functions")

    modules = {
        "rboto": SimpleNamespace(
            __version__="0.1.0", __all__=["helper"], helper=invalid_helper, STSClient=STSClient,
        ),
        "rboto_sts": SimpleNamespace(STSClient=STSClient),
        "rboto_sts._native": SimpleNamespace(__file__="/installed/rboto_sts/_native.so"),
    }
    installed = SimpleNamespace(
        version="0.1.0",
        files=[
            f"rboto_sts/{name}" for name in ("py.typed", "_native.pyi", "client.py", "types.py")
        ],
    )
    def distribution(name: str) -> SimpleNamespace:
        return installed

    monkeypatch.setattr(checker, "distribution", distribution)
    # Patch the script's module reference without modifying importlib globally.
    monkeypatch.setattr(checker, "importlib", SimpleNamespace(import_module=modules.__getitem__))
    checker.smoke(catalog)
    assert created == [("us-east-1", "http://127.0.0.1:9")]


def test_registration_list_covers_every_release_project(tmp_path: Path) -> None:
    checker = load_script("release_catalog")
    checker.write_catalog("0.1.0", "a" * 40, tmp_path)
    catalog = json.loads((tmp_path / "release.json").read_text())
    rows = (tmp_path / "trusted-publishers.csv").read_text().splitlines()
    assert len(rows) == len(catalog["services"]) + 2
    assert all(row.endswith(",ferrumio,rboto,release.yml,pypi") for row in rows[1:])
    assert catalog["commit"] == "a" * 40
    assert catalog["version"] == "0.1.0"
    batches = catalog["batches"]
    services = [name for batch in batches for name in batch["services"]]
    assert sorted(services) == sorted(service["service"] for service in catalog["services"])
    assert len(services) == len(set(services))
    assert all(1 <= len(batch["services"]) <= 6 for batch in batches)


@pytest.mark.parametrize("count", [1, 6, 7, 12, 13, 34, 35, 36, 37, 101])
def test_service_batches_cover_new_services_without_small_tail_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int,
) -> None:
    checker = load_script("release_catalog")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    services: list[SimpleNamespace] = []
    for index in range(count):
        name = f"service-{index:03}"
        source = tmp_path / "crates" / f"rboto-{name}" / "src/generated.rs"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x" * (index + 1) * 100)
        services.append(SimpleNamespace(service_id=name, distribution_name=f"rboto-{name}"))
    batches = checker.service_batches(services)
    assert batches == checker.service_batches(list(reversed(services)))
    names = [name for batch in batches for name in batch["services"]]
    assert sorted(names) == sorted(service.service_id for service in services)
    assert len(names) == len(set(names))
    sizes = [len(batch["services"]) for batch in batches]
    assert max(sizes) <= 6 and max(sizes) - min(sizes) <= 1
    # Spread the largest native adapters across different jobs.
    largest = {service.service_id for service in services[-len(batches):]}
    assert all(len(largest.intersection(batch["services"])) == 1 for batch in batches)
    if count == 34:
        assert sorted(sizes) == [5, 5, 6, 6, 6, 6]


def test_empty_release_cannot_produce_a_successful_empty_matrix() -> None:
    checker = load_script("release_catalog")
    with pytest.raises(ValueError, match="no services"):
        checker.service_batches([])
