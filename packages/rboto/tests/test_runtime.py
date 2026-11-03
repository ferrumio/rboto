import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Callable, cast

import pytest
from rboto import configure_runtime, runtime


@pytest.fixture(autouse=True)
def fresh_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_configured", None)
    monkeypatch.setattr(runtime, "_frozen", None)
    monkeypatch.delenv("TOKIO_WORKER_THREADS", raising=False)


def freeze(available: int = 32) -> int:
    return cast(Callable[[int], int], getattr(runtime, "_freeze_worker_threads"))(available)


@pytest.mark.parametrize(("available", "expected"), [(1, 1), (2, 2), (32, 2)])
def test_bounded_default(available: int, expected: int) -> None:
    assert freeze(available) == expected


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_explicit_setting_wins_over_environment(
    workers: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKIO_WORKER_THREADS", "invalid")
    configure_runtime(worker_threads=workers)
    assert freeze() == workers
    assert os.environ["TOKIO_WORKER_THREADS"] == "invalid"


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_environment_override(workers: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKIO_WORKER_THREADS", str(workers))
    assert freeze(1) == workers


@pytest.mark.parametrize("value", ["04", "+4"])
def test_environment_preserves_decimal_spellings(
    value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKIO_WORKER_THREADS", value)
    assert freeze() == 4


@pytest.mark.parametrize("value", [0, -1, sys.maxsize + 1])
def test_invalid_counts(value: int) -> None:
    with pytest.raises(ValueError, match="worker_threads"):
        configure_runtime(worker_threads=value)
    assert freeze() == 2


@pytest.mark.parametrize("value", [True, False, "2", 2.0, None])
def test_non_integer_counts(value: object) -> None:
    with pytest.raises(TypeError, match="worker_threads"):
        configure_runtime(worker_threads=cast(int, value))


@pytest.mark.parametrize("value", ["", "0", "-1", "2.5", "two", " 2 ", "٢", str(sys.maxsize + 1)])
def test_invalid_environment_can_be_corrected(
    value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKIO_WORKER_THREADS", value)
    with pytest.raises(ValueError):
        freeze()
    monkeypatch.setenv("TOKIO_WORKER_THREADS", "3")
    assert freeze() == 3


def test_can_change_before_first_use() -> None:
    configure_runtime(worker_threads=1)
    configure_runtime(worker_threads=4)
    assert freeze() == 4


def test_freezing_is_idempotent_and_rejects_late_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert freeze() == 2
    configure_runtime(worker_threads=2)
    with pytest.raises(RuntimeError, match="before the first native operation"):
        configure_runtime(worker_threads=4)
    monkeypatch.setenv("TOKIO_WORKER_THREADS", "4")
    # Later extensions inherit the frozen policy, regardless of affinity or env changes.
    assert freeze(1) == 2


def test_concurrent_first_use_resolves_one_policy() -> None:
    barrier = Barrier(12)

    def start(index: int) -> int:
        barrier.wait(timeout=10)
        return freeze(1 if index % 2 else 32)

    with ThreadPoolExecutor(max_workers=12) as pool:
        counts = list(pool.map(start, range(12)))
    assert len(set(counts)) == 1
    configure_runtime(worker_threads=counts[0])
    with pytest.raises(RuntimeError):
        configure_runtime(worker_threads=counts[0] + 1)
