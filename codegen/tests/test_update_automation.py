import hashlib
import importlib.util
import json
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from urllib.error import URLError

import pytest
from rboto_codegen.cli_generator import generate_cli_layout
from rboto_codegen.manifests import generate_manifests
from rboto_codegen.registry import get_service, list_services
from rboto_codegen.rust_crate import find_crate

ROOT = Path(__file__).parents[2]


def test_crate_lookup_uses_the_configured_cargo_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CARGO_HOME", str(tmp_path))
    descriptor = get_service("kms")
    with pytest.raises(FileNotFoundError, match="run cargo fetch"):
        find_crate(descriptor)
    crate = tmp_path / "registry/src/index" / (
        f"{descriptor.rust_crate}-{descriptor.rust_crate_version}"
    )
    crate.mkdir(parents=True)
    assert find_crate(descriptor) == crate


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_repository(root: Path) -> None:
    for relative in ("Cargo.toml", "codegen/pyproject.toml"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((ROOT / relative).read_text())
    core = root / "packages/rboto/pyproject.toml"
    core.parent.mkdir(parents=True)
    core.write_text(
        '[project]\nname = "rboto"\nversion = "0.1.0"\n'
        '[project.optional-dependencies]\nkms = ["rboto-kms==0.1.0"]\n'
        'all = ["rboto-kms==0.1.0"]\n'
    )


def test_manifests_bootstrap_a_crate_that_is_not_downloaded(tmp_path: Path) -> None:
    seed_repository(tmp_path)
    descriptor = replace(get_service("kms"), rust_crate_version="99.0.0")
    cargo, package = generate_manifests(descriptor, tmp_path)
    assert tomllib.loads(cargo.read_text())["dependencies"]["aws-sdk-kms"] == "=99.0.0"
    project = tomllib.loads(package.read_text())["project"]
    assert project["version"] == "0.1.0"
    assert project["dependencies"] == ["rboto==0.1.0"]
    assert not (tmp_path / "codegen/models").exists()
    assert not (cargo.parent / "src/generated.rs").exists()


@pytest.mark.parametrize("broken_pin", [None, "service", "extra", "all"])
def test_version_bump_keeps_package_dependencies_synchronized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken_pin: str | None
) -> None:
    seed_repository(tmp_path)
    setter = load_script("set_version")
    monkeypatch.setattr(setter, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["set_version.py", "0.2.1"])
    setter.main()
    _, package = generate_manifests(get_service("kms"), tmp_path)
    checker = load_script("check_version")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_version.py", "--expected", "0.2.1"])
    checker.main()

    if broken_pin is None:
        project = tomllib.loads(package.read_text())["project"]
        assert project["version"] == "0.2.1"
        assert project["dependencies"] == ["rboto==0.2.1"]
        return
    if broken_pin == "service":
        package.write_text(package.read_text().replace("rboto==0.2.1", "rboto==0.1.0"))
    else:
        core = tmp_path / "packages/rboto/pyproject.toml"
        old = f'{broken_pin if broken_pin == "all" else "kms"} = ["rboto-kms==0.2.1"]'
        core.write_text(core.read_text().replace(old, old.replace("0.2.1", "0.1.0")))
    with pytest.raises(SystemExit, match="must"):
        checker.main()


@pytest.mark.parametrize("broken", [None, "version", "pin"])
def test_cli_bootstrap_and_version_checks_follow_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str | None
) -> None:
    seed_repository(tmp_path)
    setter = load_script("set_version")
    monkeypatch.setattr(setter, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["set_version.py", "0.2.1"])
    setter.main()
    generate_manifests(get_service("kms"), tmp_path)
    generate_cli_layout(tmp_path)
    manifest = tmp_path / "cli/Cargo.toml"
    assert tomllib.loads(manifest.read_text())["workspace"]["package"]["version"] == "0.2.1"
    checker = load_script("check_version")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_version.py", "--expected", "0.2.1"])
    if broken is None:
        checker.main()
    else:
        text = manifest.read_text()
        old, new = (
            ('version = "0.2.1"', 'version = "0.1.0"')
            if broken == "version"
            else ('aws-config = "=1.13.0"', 'aws-config = "=0.0.0"')
        )
        assert old in text
        manifest.write_text(text.replace(old, new))
        with pytest.raises(SystemExit, match="synchronized|pin differs"):
            checker.main()


@pytest.mark.parametrize("version", ["0.2.1rc1", "0.2.1-rc.1", "01.2.3", "v0.2.1", "0.2"])
def test_invalid_package_version_does_not_modify_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    seed_repository(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    setter = load_script("set_version")
    monkeypatch.setattr(setter, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["set_version.py", version])
    with pytest.raises(SystemExit, match="invalid version"):
        setter.main()
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("fail_second_model", [False, True])
def test_update_can_prepare_new_pins_without_partial_descriptor_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_second_model: bool
) -> None:
    seed_repository(tmp_path)
    services = tmp_path / "codegen/src/rboto_codegen/services"
    services.mkdir(parents=True)
    for service in ("kms", "signin"):
        (services / f"{service}.toml").write_text(
            (ROOT / "codegen/src/rboto_codegen/services" / f"{service}.toml").read_text()
        )
    metadata = 'smithy_rs_revision = "new-revision"\n'
    for crate in (
        "aws-sdk-kms", "aws-sdk-signin", "aws-config", "aws-smithy-async",
        "aws-smithy-runtime-api", "aws-smithy-types",
    ):
        metadata += f'[crates.{crate}]\nversion = "99.0.0"\nmodel_hash = "new-hash"\n'
    model = b'{"smithy":"2.0","shapes":{}}'

    def fetch(url: str) -> bytes:
        if url.endswith("/versions.toml"):
            return metadata.encode()
        if fail_second_model and url.endswith("/signin.json"):
            raise URLError("model download failed")
        assert url.endswith(("/kms.json", "/signin.json"))
        return model

    updater = load_script("update_aws_models")
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setattr(updater, "SERVICE_DIR", services)
    monkeypatch.setattr(updater, "fetch", fetch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    if fail_second_model:
        with pytest.raises(URLError):
            updater.update("release-2026-10-26")
        assert all(path.read_bytes() == content for path, content in before.items())
        return
    updater.update("release-2026-10-26")
    for descriptor in list_services(services):
        assert descriptor.rust_crate_version == "99.0.0"
        assert descriptor.model_sha256 == hashlib.sha256(model).hexdigest()
        cargo, _ = generate_manifests(descriptor, tmp_path)
        pin = tomllib.loads(cargo.read_text())["dependencies"][descriptor.rust_crate]
        assert pin == "=99.0.0"


@pytest.mark.parametrize("release", ["main", "../main", "release-2026-02-30", "release-2026-1-1"])
def test_invalid_release_is_rejected_before_fetching(
    monkeypatch: pytest.MonkeyPatch, release: str
) -> None:
    updater = load_script("update_aws_models")

    def fetch(url: str) -> bytes:
        pytest.fail(f"invalid release must not fetch {url}")

    monkeypatch.setattr(updater, "fetch", fetch)
    with pytest.raises(ValueError):
        updater.update(release)


@pytest.mark.parametrize("branch_moved", [False, True])
def test_bot_dispatches_validation_on_its_commit_and_never_a_release(
    monkeypatch: pytest.MonkeyPatch, branch_moved: bool
) -> None:
    dispatcher = load_script("dispatch_update_checks")
    sha = "a" * 40
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert kwargs.get("check") is True
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps({"sha": "b" * 40 if branch_moved else sha})
        )

    monkeypatch.setattr(subprocess, "run", run)
    if branch_moved:
        with pytest.raises(RuntimeError, match="branch moved"):
            dispatcher.dispatch("owner/repo", "automation/models", sha)
        assert len(calls) == 1
        return
    dispatcher.dispatch("owner/repo", "automation/models", sha)
    dispatched = [args for args in calls if args[1:3] == ["workflow", "run"]]
    assert {args[3] for args in dispatched} == {
        "ci.yml", "generated-check.yml", "integration.yml",
    }
    assert all(args[-2:] == ["--ref", "automation/models"] for args in dispatched)


def test_existing_update_pull_request_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    publisher = load_script("publish_model_update")

    def run(*args: str) -> str:
        assert args[:3] == ("gh", "pr", "list")
        return '[{"url":"https://github.com/owner/repo/pull/1"}]'

    monkeypatch.setattr(publisher, "run", run)
    assert publisher.ensure_pull_request("owner/repo", "branch", "main", "update") == (
        "https://github.com/owner/repo/pull/1"
    )


@pytest.mark.parametrize("policy_denial", [False, True])
def test_only_bot_pr_policy_denial_allows_manual_fallback(
    monkeypatch: pytest.MonkeyPatch, policy_denial: bool
) -> None:
    publisher = load_script("publish_model_update")

    def run(*args: str) -> str:
        if args[:3] == ("gh", "pr", "list"):
            return "[]"
        assert args[:3] == ("gh", "pr", "create")
        assert Path(args[args.index("--body-file") + 1]).read_text() == ""
        message = (
            "GraphQL: GitHub Actions is not permitted to create or approve pull requests"
            if policy_denial else "HTTP 502: server unavailable"
        )
        raise subprocess.CalledProcessError(1, args, stderr=message)

    monkeypatch.setattr(publisher, "run", run)
    if policy_denial:
        assert publisher.ensure_pull_request("owner/repo", "branch", "main", "update") == ""
    else:
        with pytest.raises(subprocess.CalledProcessError):
            publisher.ensure_pull_request("owner/repo", "branch", "main", "update")


@pytest.mark.parametrize("existing_branch", [False, True])
def test_generated_commit_is_pushed_and_validatable_when_bot_prs_are_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_branch: bool
) -> None:
    publisher = load_script("publish_model_update")
    monkeypatch.chdir(tmp_path)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True,
        ).stdout.strip()

    remote = tmp_path / "remote.git"
    git("init", "--bare", str(remote))
    git("init", "--initial-branch=main", ".")
    git("config", "--local", "user.name", "Test")
    git("config", "--local", "user.email", "test@example.com")
    git("remote", "add", "origin", str(remote))
    for relative in publisher.UPDATE_PATHS:
        path = tmp_path / relative
        if not path.suffix:
            path = path / "tracked.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("initial\n")
        git("add", "--", relative)
    git("commit", "-m", "initial")
    initial = git("rev-parse", "HEAD")
    branch = "automation/service-models-release-2026-10-26"
    if existing_branch:
        git("push", "origin", f"HEAD:refs/heads/{branch}")
    (tmp_path / "Cargo.lock").write_text("updated\n")
    (tmp_path / "cli/Cargo.lock").write_text("updated CLI lock\n")
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> str:
        calls.append(args)
        if args[0] == "git":
            return git(*args[1:])
        if args[:3] == ("gh", "pr", "list"):
            return "[]"
        if args[:3] == ("gh", "auth", "setup-git"):
            return ""
        assert args[:3] == ("gh", "pr", "create")
        raise subprocess.CalledProcessError(
            1, args, stderr="GitHub Actions is not permitted to create pull requests",
        )

    monkeypatch.setattr(publisher, "run", run)
    result = publisher.publish("owner/repo", branch, "main", "update")
    assert result["changed"] == "true"
    assert result["branch"] == branch
    assert result["pull_request_url"] == ""
    assert "automation%2Fservice-models-" in result["compare_url"]
    remote_sha = git("ls-remote", "--heads", "origin", f"refs/heads/{branch}").split()[0]
    assert result["sha"] == remote_sha == git("rev-parse", "HEAD")
    assert remote_sha != initial
    assert git("--git-dir", str(remote), "show", f"{remote_sha}:Cargo.lock") == "updated"
    assert git("--git-dir", str(remote), "show", f"{remote_sha}:cli/Cargo.lock") == (
        "updated CLI lock"
    )
    push = next(args for args in calls if args[:2] == ("git", "push"))
    assert push[2] == f"--force-with-lease=refs/heads/{branch}:{initial if existing_branch else ''}"
    calls.clear()
    assert publisher.publish("owner/repo", branch, "main", "update") == {"changed": "false"}
    assert not any(args[:2] == ("git", "push") or args[0] == "gh" for args in calls)
