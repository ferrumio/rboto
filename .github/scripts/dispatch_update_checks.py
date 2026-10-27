"""Start validation on the generated branch when the bot token cannot trigger PR CI."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import cast
from urllib.parse import quote

WORKFLOWS = ("ci.yml", "generated-check.yml", "integration.yml")


def dispatch(repository: str, branch: str, expected_sha: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None:
        raise ValueError("expected a full commit SHA")
    for workflow in WORKFLOWS:
        # Refuse to validate another commit if the automation branch moved.
        response = subprocess.run(
            ["gh", "api", f"repos/{repository}/commits/{quote(branch, safe='')}"],
            check=True, capture_output=True, text=True,
        )
        commit = cast(dict[str, object], json.loads(response.stdout))
        if commit.get("sha") != expected_sha:
            raise RuntimeError("the update branch moved before validation was dispatched")
        subprocess.run(
            ["gh", "workflow", "run", workflow, "--repo", repository, "--ref", branch],
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    dispatch(args.repository, args.branch, args.sha)


if __name__ == "__main__":
    main()
