from __future__ import annotations

import json
from pathlib import Path

import pytest
from rboto_evals.catalog import TASKS, Task, documents
from rboto_evals.grading import source_policy
from rboto_evals.runner import Config, Json, parse_submission, plan, regrade, run


def fake_docs(root: Path) -> None:
    for task in TASKS:
        for name in task.references:
            path = root / f"docs/reference/{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Public API reference.")
        for name in task.guides:
            path = root / f"docs/guides/{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Public user guide.")


def test_plan_pairs_and_balances_conditions() -> None:
    config = Config(model="fixed-version")
    runs = plan(config)
    assert len(runs) == 20
    assert runs == plan(config)
    for index in range(0, len(runs), 2):
        first, second = runs[index : index + 2]
        assert first["case_seed"] == second["case_seed"]
        assert first["task"] == second["task"]
        assert first["condition"] != second["condition"]
    assert runs[0]["condition"] != runs[10]["condition"]


def test_hard_call_budget_keeps_unrun_cases(tmp_path: Path) -> None:
    fake_docs(tmp_path)
    calls: list[list[Json]] = []

    def provider(messages: list[Json]) -> Json:
        calls.append(messages.copy())
        return {
            "text": '{"code": "pass", "dependencies": []}',
            "usage": {
                "inputTokens": 50,
                "outputTokens": 10,
            },
        }

    def grader(task: Task, submission: Json, seed: int) -> Json:
        return {"ok": len(calls) > 1, "reason": "behavior"}

    report = run(
        tmp_path,
        tmp_path / "out",
        Config(model="test", max_calls=3),
        provider,
        grader,
        {"image": "test"},
    )
    assert len(calls) == 3
    assert [len(messages) for messages in calls] == [1, 1, 1]
    assert len(report["runs"]) == 20
    assert report["runs"][0]["status"] == "incomplete"
    assert len(report["runs"][0]["attempts"]) == 1
    assert sum(not item["attempts"] for item in report["runs"]) == 17
    assert json.loads((tmp_path / "out/results.json").read_text()) == report
    assert sum(s["output_tokens"] for s in report["summary"].values()) == 30
    with pytest.raises(FileExistsError):
        run(tmp_path, tmp_path / "out", Config(model="test"), provider, grader, {})


def test_deadline_does_not_drop_failures(tmp_path: Path) -> None:
    fake_docs(tmp_path)
    ticks = 0.0
    calls = 0

    def provider(messages: list[Json]) -> Json:
        nonlocal calls, ticks
        calls += 1
        ticks += 1000
        raise RuntimeError("provider details must not be recorded")

    report = run(
        tmp_path,
        tmp_path / "out",
        Config(model="test"),
        provider,
        lambda *args: {"ok": True},
        {},
        clock=lambda: ticks,
    )
    assert calls == 1
    assert report["runs"][0]["status"] == "incomplete"
    assert report["runs"][0]["attempts"][0]["grade"]["reason"] == "infrastructure"
    assert "provider details" not in json.dumps(report)
    assert report["runs"][1]["stop_reason"] == "deadline"


def test_oversized_context_makes_no_calls(tmp_path: Path) -> None:
    fake_docs(tmp_path)

    def forbidden(messages: list[Json]) -> Json:
        pytest.fail("must not call the model")

    report = run(
        tmp_path,
        tmp_path / "out",
        Config(model="test", max_input_bytes=1),
        forbidden,
        lambda *args: {"ok": True},
        {},
    )
    assert all(item["stop_reason"] == "input_budget" for item in report["runs"])


def test_context_contains_no_private_fixtures(tmp_path: Path) -> None:
    fake_docs(tmp_path)
    (tmp_path / "private.txt").write_text("SECRET_GRADING_CASE")
    for task in TASKS:
        baseline = documents(tmp_path, task, "references")
        guided = documents(tmp_path, task, "guides")
        assert guided[: len(baseline)] == baseline
        assert len(guided) > len(baseline)
        assert "SECRET_GRADING_CASE" not in json.dumps(guided)


@pytest.mark.parametrize("text", ("{}", "```json\n{}\n```", '{"code": 1, "dependencies": []}'))
def test_invalid_submission(text: str) -> None:
    with pytest.raises(ValueError):
        parse_submission(text)


def test_json_fence_is_not_an_api_failure() -> None:
    assert parse_submission('```json\n{"code": "pass", "dependencies": []}\n```') == {
        "code": "pass",
        "dependencies": [],
    }


def test_corrections_follow_all_first_attempts(tmp_path: Path) -> None:
    fake_docs(tmp_path)
    lengths: list[int] = []

    def provider(messages: list[Json]) -> Json:
        lengths.append(len(messages))
        return {"text": '{"code": "pass", "dependencies": []}'}

    report = run(
        tmp_path,
        tmp_path / "out",
        Config(model="test", max_calls=21),
        provider,
        lambda *args: {"ok": len(lengths) > 20},
        {},
    )
    assert lengths == [1] * 20 + [3]
    assert report["runs"][0]["status"] == "passed"
    assert report["runs"][1]["status"] == "incomplete"


def test_interrupt_is_checkpointed(tmp_path: Path) -> None:
    fake_docs(tmp_path)

    def provider(messages: list[Json]) -> Json:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(
            tmp_path,
            tmp_path / "out",
            Config(model="test"),
            provider,
            lambda *args: {"ok": True},
            {},
        )
    report = json.loads((tmp_path / "out/results.json").read_text())
    assert report["runs"][0]["attempts"][0]["grade"]["reason"] == "interrupted"
    assert report["runs"][0]["status"] == "incomplete"


def test_diagnostic_replay_preserves_original_score(tmp_path: Path) -> None:
    fake_docs(tmp_path)
    run(
        tmp_path,
        tmp_path / "original",
        Config(model="test", max_calls=1),
        lambda messages: {"text": '{"code": "pass", "dependencies": []}'},
        lambda *args: {"ok": False, "reason": "dependency_selection"},
        {},
    )
    before = (tmp_path / "original/results.json").read_bytes()
    dependencies: list[list[str]] = []

    def grader(task: Task, submission: Json, seed: int) -> Json:
        dependencies.append(submission["dependencies"])
        return {"ok": True}

    result = regrade(
        tmp_path / "original",
        tmp_path / "diagnostic.json",
        grader,
        {},
        canonical_dependencies=True,
    )
    assert result["cases"][0]["original_dependencies"] == []
    assert result["cases"][0]["original_grade"]["ok"] is False
    assert result["cases"][0]["grade"]["ok"] is True
    assert dependencies[0] != []
    assert (tmp_path / "original/results.json").read_bytes() == before


@pytest.mark.parametrize(
    "code",
    (
        "value = client._native",
        "getattr(value, 'x')",
        "value = 1  # type: ignore",
    ),
)
def test_forbidden_shortcuts(code: str) -> None:
    assert source_policy(TASKS[0], code)
