"""Publish a generated branch, retaining a manual PR path when bot PRs are forbidden."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import cast
from urllib.parse import quote

UPDATE_PATHS = (
    "Cargo.toml", "Cargo.lock", "cli", "codegen", "crates", "packages", "pyrightconfig.json",
)


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def ensure_pull_request(repository: str, branch: str, base: str, title: str) -> str:
    pulls = cast(list[dict[str, object]], json.loads(run(
        "gh", "pr", "list", "--repo", repository, "--head", branch,
        "--base", base, "--state", "open", "--json", "url",
    )))
    if pulls:
        return str(pulls[0]["url"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as body:
        try:
            return run(
                "gh", "pr", "create", "--repo", repository, "--head", branch,
                "--base", base, "--title", title, "--body-file", body.name,
            )
        except subprocess.CalledProcessError as error:
            message = (error.stderr or "").lower()
            if "github actions" not in message or not any(
                phrase in message for phrase in (
                    "not permitted to create", "not allowed to create",
                    "does not allow github actions to create",
                )
            ):
                raise
            print(
                "::warning::Repository policy blocks bot PR creation; "
                "open the generated branch manually."
            )
            return ""


def publish(repository: str, branch: str, base: str, title: str) -> dict[str, str]:
    if not branch.startswith("automation/service-models-"):
        raise ValueError("updates must use the service-model automation branch namespace")
    run("git", "add", "--", *UPDATE_PATHS)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if changed.returncode == 0:
        return {"changed": "false"}
    if changed.returncode != 1:
        changed.check_returncode()
    run("git", "switch", "-C", branch)
    run("git", "config", "--local", "user.name", "github-actions[bot]")
    run(
        "git", "config", "--local", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    run("git", "commit", "-m", title)
    sha = run("git", "rev-parse", "HEAD")
    ref = f"refs/heads/{branch}"
    run("gh", "auth", "setup-git")
    remote = run("git", "ls-remote", "--heads", "origin", ref)
    expected = remote.split()[0] if remote else ""
    run("git", "push", f"--force-with-lease={ref}:{expected}", "origin", f"HEAD:{ref}")
    pull_request = ensure_pull_request(repository, branch, base, title)
    compare = (
        f"https://github.com/{repository}/compare/"
        f"{quote(base, safe='')}...{quote(branch, safe='')}?expand=1"
    )
    return {
        "changed": "true", "sha": sha, "branch": branch,
        "pull_request_url": pull_request, "compare_url": compare,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    result = publish(args.repository, args.branch, args.base, args.title)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
        if result["changed"] == "false":
            summary.write("No model or package changes were needed.\n")
        else:
            summary.write(f"Generated commit: `{result['sha']}`.\n\n")
            if result["pull_request_url"]:
                summary.write(f"Pull request: {result['pull_request_url']}\n")
            else:
                summary.write(
                    "Organization/repository policy blocks bot pull requests. "
                    f"[Open the generated branch as a PR]({result['compare_url']}).\n"
                )
            summary.write("\nValidation is dispatched separately on this branch.\n")


if __name__ == "__main__":
    main()
