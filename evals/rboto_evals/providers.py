"""Provider and isolated grader adapters. Neither is used by ordinary CI."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from .catalog import Task
from .runner import Config, Json


class Bedrock:
    def __init__(self, config: Config) -> None:
        self.config = config

    def __call__(self, messages: list[Json]) -> Json:
        request = {
            "modelId": self.config.model,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": self.config.max_output_tokens,
                "temperature": 0,
            },
        }
        with tempfile.TemporaryDirectory(prefix="rboto-eval-model-") as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(request))
            process = subprocess.run(
                [
                    "aws",
                    "bedrock-runtime",
                    "converse",
                    "--region",
                    self.config.region,
                    "--cli-input-json",
                    f"file://{path}",
                    "--output",
                    "json",
                    "--cli-connect-timeout",
                    "10",
                    "--cli-read-timeout",
                    "60",
                ],
                env={**os.environ, "AWS_MAX_ATTEMPTS": "1", "AWS_PAGER": ""},
                capture_output=True,
                text=True,
                check=True,
                timeout=75,
            )
        response: Json = json.loads(process.stdout)
        return {
            "text": "".join(
                block.get("text", "") for block in response["output"]["message"]["content"]
            ),
            "usage": response["usage"],
            "stopReason": response["stopReason"],
        }


class DockerGrader:
    def __init__(self, command: list[str], image: str, version: str) -> None:
        self.command = command
        self.image = image
        self.version = version

    def __call__(self, task: Task, submission: Json, seed: int) -> Json:
        with tempfile.TemporaryDirectory(prefix="rboto-eval-candidate-") as directory:
            project = Path(directory)
            project.chmod(0o755)
            (project / "solution.py").write_text(submission["code"])
            (project / "submission.json").write_text(json.dumps(submission))
            (project / "requirements.txt").write_text("\n".join(submission["dependencies"]) + "\n")
            name = "rboto-eval-" + uuid.uuid4().hex
            command = [
                *self.command,
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--user",
                "65534:65534",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--cpus",
                "1",
                "--memory",
                "1g",
                "--memory-swap",
                "1g",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=256m",
                "--mount",
                f"type=bind,src={project},dst=/work,readonly",
                self.image,
                "python",
                "-m",
                "rboto_evals.grading",
                task.name,
                "/work",
                str(seed),
                self.version,
            ]
            try:
                process = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=50,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                subprocess.run(
                    [*self.command, "rm", "-f", name],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                return {"ok": False, "reason": "grader_timeout"}
            if process.returncode:
                return {"ok": False, "reason": "grader_process", "exit_code": process.returncode}
            try:
                result: Json = json.loads(process.stdout)
            except ValueError:
                return {"ok": False, "reason": "grader_protocol"}
            return result
