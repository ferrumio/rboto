"""Bounded code-generation experiment; model access is supplied by the caller."""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .catalog import TASKS, Task, documents, prompt

Json = dict[str, Any]
Provider = Callable[[list[Json]], Json]
Grader = Callable[[Task, Json, int], Json]


@dataclass(frozen=True)
class Config:
    model: str
    region: str = "eu-west-2"
    version: str = "0.2.0"
    repeats: int = 2
    attempts: int = 2
    max_calls: int = 40
    max_output_tokens: int = 2048
    max_input_bytes: int = 65536
    deadline_seconds: int = 900
    seed: int = 71

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, int) and name != "seed" and value < 1:
                raise ValueError(f"{name} must be positive")
        if self.attempts > 2:
            raise ValueError("pilot allows at most one correction")
        if not self.model or not self.version:
            raise ValueError("model and SDK version must be explicit")


def plan(config: Config) -> list[Json]:
    """Pair conditions within each repetition, balancing their execution order."""
    config.validate()
    rng = random.Random(config.seed)
    tasks = list(TASKS)
    rng.shuffle(tasks)
    runs: list[Json] = []
    for repetition in range(config.repeats):
        for index, task in enumerate(tasks):
            conditions = ["references", "guides"]
            if (index + repetition) % 2:
                conditions.reverse()
            case_seed = config.seed + repetition * 100 + index
            for condition in conditions:
                runs.append(
                    {
                        "task": task.name,
                        "condition": condition,
                        "repetition": repetition + 1,
                        "case_seed": case_seed,
                        "status": "not_run",
                        "attempts": [],
                    }
                )
    return runs


def parse_submission(text: str) -> Json:
    # Markdown fences are presentation, not an SDK usage error. Accept exactly
    # one enclosing JSON fence, never prose or arbitrary embedded code blocks.
    fenced = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", text, re.DOTALL)
    if fenced:
        text = fenced[1]
    value = json.loads(text)
    if (
        not isinstance(value, dict)
        or set(value) != {"code", "dependencies"}
        or not isinstance(value["code"], str)
        or not isinstance(value["dependencies"], list)
        or not all(isinstance(item, str) for item in value["dependencies"])
    ):
        raise ValueError("expected JSON code and dependencies")
    return value


def summarize(runs: list[Json]) -> Json:
    result: Json = {}
    for condition in ("references", "guides"):
        selected = [run for run in runs if run["condition"] == condition]
        attempts = [attempt for run in selected for attempt in run["attempts"]]
        first = sum(
            bool(run["attempts"] and run["attempts"][0]["grade"].get("ok")) for run in selected
        )
        eventual = sum(run["status"] == "passed" for run in selected)
        result[condition] = {
            "planned": len(selected),
            "first_attempt_passed": first,
            "eventual_passed": eventual,
            "not_run": sum(not run["attempts"] for run in selected),
            "incomplete": sum(run["status"] == "incomplete" for run in selected),
            "correction_attempts": sum(max(0, len(run["attempts"]) - 1) for run in selected),
            "model_calls": len(attempts),
            "input_tokens": sum(a.get("usage", {}).get("inputTokens", 0) for a in attempts),
            "output_tokens": sum(a.get("usage", {}).get("outputTokens", 0) for a in attempts),
            "model_seconds": sum(a["model_seconds"] for a in attempts),
            "attempts_without_token_usage": sum(not a.get("usage") for a in attempts),
            "failure_reasons": dict(
                Counter(
                    a["grade"].get("reason", "unknown")
                    for a in attempts
                    if not a["grade"].get("ok")
                )
            ),
            "typing_error_codes": dict(
                sum(
                    (
                        Counter(a["grade"].get("typing", {}).get("error_codes", {}))
                        for a in attempts
                    ),
                    Counter(),
                )
            ),
            "provided_document_bytes": sum(run.get("document_bytes", 0) for run in selected),
            "per_repetition": {
                str(rep): {
                    "passed": sum(
                        r["status"] == "passed" for r in selected if r["repetition"] == rep
                    ),
                    "planned": sum(r["repetition"] == rep for r in selected),
                }
                for rep in sorted({r["repetition"] for r in selected})
            },
        }
    return result


def run(
    root: Path,
    output: Path,
    config: Config,
    provider: Provider,
    grader: Grader,
    environment: Json,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Json:
    config.validate()
    # Refuse accidental replacement of evidence from an earlier experiment.
    output.mkdir(parents=True, exist_ok=False)
    runs = plan(config)
    report: Json = {
        "schema_version": 1,
        "started_at": datetime.now(UTC).isoformat(),
        "config": asdict(config),
        "environment": environment,
        "tools": [],
        "temperature": 0,
        "documentation_reads": 0,
        "context_delivery": "files supplied in prompt; no retrieval tools",
        "runner_files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).parent.glob("*.py"))
        },
        "runs": runs,
    }
    started = clock()
    calls = 0
    contexts: dict[str, list[Json]] = {}

    def save() -> None:
        report["summary"] = summarize(runs)
        report["elapsed_seconds"] = clock() - started
        temporary = output / "results.tmp"
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(output / "results.json")

    save()
    try:
        for item in runs:
            task = next(task for task in TASKS if task.name == item["task"])
            docs = documents(root, task, item["condition"])
            text = prompt(task, config.version, docs)
            item["documents"] = [{k: v for k, v in doc.items() if k != "content"} for doc in docs]
            item["document_bytes"] = sum(len(doc["content"].encode()) for doc in docs)
            item["prompt_sha256"] = hashlib.sha256(text.encode()).hexdigest()
            identifier = f"{task.name}-{item['condition']}-{item['repetition']}"
            folder = output / identifier
            folder.mkdir()
            (folder / "prompt.txt").write_text(text)
            contexts[identifier] = [{"role": "user", "content": [{"text": text}]}]
        # Give every planned task a first attempt before spending on corrections.
        for attempt_index in range(config.attempts):
            for item in runs:
                if item["status"] == "passed" or (attempt_index and not item["attempts"]):
                    continue
                task = next(task for task in TASKS if task.name == item["task"])
                identifier = f"{task.name}-{item['condition']}-{item['repetition']}"
                folder = output / identifier
                messages = contexts[identifier]
                reason = None
                if calls >= config.max_calls:
                    reason = "call_budget"
                elif clock() - started >= config.deadline_seconds:
                    reason = "deadline"
                elif len(json.dumps(messages).encode()) > config.max_input_bytes:
                    reason = "input_budget"
                if reason:
                    item["status"] = "incomplete" if item["attempts"] else "not_run"
                    item["stop_reason"] = reason
                    continue
                calls += 1
                attempt: Json = {
                    "number": attempt_index + 1,
                    "usage": {},
                    "model_seconds": 0,
                    "grade": {"ok": False, "reason": "interrupted"},
                }
                attempt_started = clock()
                item["attempts"].append(attempt)
                item["status"] = "incomplete"
                save()
                try:
                    reply = provider(messages)
                    attempt["model_seconds"] = clock() - attempt_started
                    attempt["usage"] = reply.get("usage", {})
                    attempt["stop_reason"] = reply.get("stopReason")
                    generated = reply["text"]
                    (folder / f"attempt-{attempt_index + 1}.txt").write_text(generated)
                    try:
                        submission = parse_submission(generated)
                    except (ValueError, TypeError):
                        checked: Json = {"ok": False, "reason": "invalid_submission"}
                    else:
                        (folder / f"attempt-{attempt_index + 1}.json").write_text(
                            json.dumps(submission, indent=2) + "\n"
                        )
                        grade_started = clock()
                        checked = grader(task, submission, item["case_seed"])
                        attempt["grading_seconds"] = clock() - grade_started
                    contexts[identifier] += [
                        {"role": "assistant", "content": [{"text": generated}]},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "text": "Correct the solution using this feedback. "
                                    "Return the same "
                                    "JSON format. No additional tools are available.\n"
                                    + json.dumps(checked),
                                }
                            ],
                        },
                    ]
                except Exception as error:
                    # Do not retain provider stderr, credentials, or account identifiers.
                    attempt["model_seconds"] = clock() - attempt_started
                    checked = {
                        "ok": False,
                        "reason": "infrastructure",
                        "error": type(error).__name__,
                    }
                except KeyboardInterrupt:
                    attempt["model_seconds"] = clock() - attempt_started
                    raise
                attempt["grade"] = checked
                item["status"] = "passed" if checked.get("ok") else "failed"
                save()
    finally:
        save()
    return report


def regrade(
    source: Path,
    output: Path,
    grader: Grader,
    environment: Json,
    *,
    canonical_dependencies: bool = False,
) -> Json:
    """Diagnostic-only replay. Never replace the experiment or its feedback history."""
    if output.exists():
        raise FileExistsError(output)
    original = json.loads((source / "results.json").read_text())
    result: Json = {
        "schema_version": 1,
        "purpose": "diagnostic replay; original experiment scores are unchanged",
        "source_results_sha256": hashlib.sha256((source / "results.json").read_bytes()).hexdigest(),
        "canonical_dependencies": canonical_dependencies,
        "environment": environment,
        "cases": [],
    }
    for item in original["runs"]:
        task = next(task for task in TASKS if task.name == item["task"])
        identifier = f"{task.name}-{item['condition']}-{item['repetition']}"
        for attempt in item["attempts"]:
            raw = source / identifier / f"attempt-{attempt['number']}.txt"
            case: Json = {
                "task": task.name,
                "condition": item["condition"],
                "repetition": item["repetition"],
                "attempt": attempt["number"],
                "original_grade": attempt["grade"],
            }
            try:
                submission = parse_submission(raw.read_text())
            except (ValueError, OSError):
                case["grade"] = {"ok": False, "reason": "unavailable_submission"}
            else:
                case["original_dependencies"] = submission["dependencies"]
                case["source_sha256"] = hashlib.sha256(submission["code"].encode()).hexdigest()
                if canonical_dependencies:
                    submission["dependencies"] = task.dependencies(original["config"]["version"])
                case["grade"] = grader(task, submission, item["case_seed"])
            result["cases"].append(case)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result
