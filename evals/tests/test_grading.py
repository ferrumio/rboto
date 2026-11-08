"""Trusted references and deliberate mistakes; no generated code or model calls."""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path

import pytest
from rboto_evals.catalog import TASKS, Task, task_named
from rboto_evals.grading import grade
from rboto_evals.runner import Json

REFERENCES = Path(__file__).parents[1] / "reference_solutions"


def reference(task: Task) -> str:
    return (REFERENCES / f"{task.name}.py").read_text()


def check(
    task: Task,
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Json:
    selected = os.environ.get("RBOTO_TEST_SERVICES")
    if selected and task.service not in selected.split(","):
        pytest.skip("client is outside this wheel job")
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_MAX_ATTEMPTS": "1",
    }.items():
        monkeypatch.setenv(key, value)
    version = importlib.metadata.version("rboto")
    (tmp_path / "solution.py").write_text(source)
    (tmp_path / "submission.json").write_text(
        json.dumps(
            {
                "code": source,
                "dependencies": task.dependencies(version),
            }
        )
    )
    return grade(task, tmp_path, 127, version)


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.name)
def test_reference(task: Task, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = check(task, reference(task), tmp_path, monkeypatch)
    assert result["ok"], result


# A passing type check must not hide wrong pagination, acknowledgements, or output handling.
MUTANTS = (
    ("s3_projection", "for bucket in response.buckets or []", "for bucket in []", "typing"),
    ("s3_projection", "return [", "return []\n        unused = [", "behavior"),
    ("dynamodb_pages", "    return labels", "            break\n    return labels", "behavior"),
    ("dynamodb_pages", "isinstance(value, str)", "isinstance(value, int)", "typing"),
    ("sqs_ack", "message.body is None", "not message.body", "behavior"),
    ("sqs_ack", "await handler(message.body)", "pass", "behavior"),
    (
        "sqs_ack",
        "await handler(message.body)",
        "try:\n                await handler(message.body)\n            except Exception:\n"
        "                return count",
        "behavior",
    ),
    ("s3_stream", "except NoSuchKeyError:", "except Exception:", "behavior"),
    (
        "s3_stream",
        "async with response.body as body:",
        "body = response.body\n        if True:",
        "behavior",
    ),
    ("s3_stream", "digest.update(chunk)", "digest.update(b'wrong')", "behavior"),
    ("bedrock_input", '"max_tokens": 32', '"max_tokens": 33', "behavior"),
    ("bedrock_input", 'return "".join(parts)', 'return parts[0] if parts else ""', "behavior"),
    (
        "bedrock_input",
        "from rboto import BedrockRuntimeClient",
        "from rboto import ImaginaryClient as BedrockRuntimeClient",
        "typing",
    ),
    ("bedrock_input", "response.output", 'response["output"]', "typing"),
    (
        "bedrock_input",
        "async with BedrockRuntimeClient",
        "client = BedrockRuntimeClient",
        "invalid_syntax",
    ),
)


@pytest.mark.parametrize("name,old,new,stage", MUTANTS)
def test_reject_mutant(
    name: str,
    old: str,
    new: str,
    stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = task_named(name)
    source = reference(task)
    assert old in source
    result = check(task, source.replace(old, new), tmp_path, monkeypatch)
    assert not result["ok"], result
    if stage == "behavior":
        assert result["typing"]["ok"], result
        assert result["behavior"]["ok"] is False, result
    else:
        assert result["reason"] == stage, result


def test_unclosed_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = task_named("s3_projection")
    source = reference(task).replace(
        'async with S3Client(region="eu-west-2", endpoint_url=endpoint_url) as client:',
        'client = S3Client(region="eu-west-2", endpoint_url=endpoint_url)\n    if True:',
    )
    result = check(task, source, tmp_path, monkeypatch)
    assert result["typing"]["ok"], result
    assert result["reason"] == "close_client", result
