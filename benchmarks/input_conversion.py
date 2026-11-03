"""Measure native input preparation without a runtime or network request.

Run outside an event loop. Valid inputs are fully converted before the common
future bridge raises "no running event loop". That fixed terminal error and
dropping the prepared request are included in both measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import statistics
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from rboto_dynamodb import _native
from rboto_dynamodb._native import DynamoDBClient, _runtime_worker_threads


def inputs() -> dict[str, dict[str, object]]:
    return {
        "small": {"table_name": "benchmark", "item": {"pk": {"s": "one"}, "n": {"n": "1"}}},
        "wide": {
            "table_name": "benchmark",
            "item": {f"field-{index}": {"s": "x" * 64} for index in range(200)},
        },
        "nested": {
            "table_name": "benchmark",
            "item": {"records": {"l": [
                {"m": {
                    "id": {"n": str(index)},
                    "tags": {"ss": ["a", "b", "c"]},
                    "detail": {"m": {"active": {"bool": True}, "text": {"s": "x" * 64}}},
                }}
                for index in range(200)
            ]}},
        },
    }


def prepare(
    method: Callable[[dict[str, object]], Awaitable[object]], parameters: dict[str, object],
) -> None:
    try:
        method(parameters)
    except RuntimeError as error:
        if str(error) != "no running event loop":
            raise
    else:
        raise AssertionError("benchmark must run outside an event loop")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--samples", type=int, default=300)
    args = parser.parse_args()
    if args.rounds < 1 or args.samples < 1:
        parser.error("rounds and samples must be positive")
    client = DynamoDBClient(region="eu-west-2", endpoint_url="http://127.0.0.1:1")
    cases = inputs()
    batches: dict[str, list[dict[str, float]]] = {name: [] for name in cases}
    for parameters in cases.values():
        for _ in range(20):
            prepare(client.put_item, parameters)
    assert _runtime_worker_threads() is None
    order = list(cases) * args.rounds
    random.Random(70).shuffle(order)
    for name in order:
        started = time.perf_counter_ns()
        cpu = time.thread_time_ns()
        for _ in range(args.samples):
            prepare(client.put_item, cases[name])
        batches[name].append({
            "wall_ns": (time.perf_counter_ns() - started) / args.samples,
            "cpu_ns": (time.thread_time_ns() - cpu) / args.samples,
        })
    assert _runtime_worker_threads() is None
    report = {
        "label": args.label,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "native_sha256": hashlib.sha256(Path(_native.__file__).read_bytes()).hexdigest(),
        "rounds": args.rounds,
        "samples_per_round": args.samples,
        "runtime_started": False,
        "median_ns": {
            name: {metric: statistics.median(row[metric] for row in rows)
                   for metric in ("wall_ns", "cpu_ns")}
            for name, rows in batches.items()
        },
        "batches": batches,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["median_ns"], indent=2))


if __name__ == "__main__":
    main()
