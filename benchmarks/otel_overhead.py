"""Compare optional telemetry using compiled DynamoDB and a loopback HTTP peer."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

from interceptors import run as run_client

MODES = (
    "unconfigured", "disabled", "middleware", "non-recording", "recording", "metrics", "combined",
)


async def worker(mode: str, requests: int, concurrency: int) -> dict[str, object]:
    if mode in {"unconfigured", "middleware"}:
        return await run_client(
            "middleware" if mode == "middleware" else "disabled", requests, concurrency, False,
        )
    from rboto.integrations.opentelemetry import configure_opentelemetry

    if mode == "disabled":
        configure_opentelemetry(enabled=False)
        return await run_client("disabled", requests, concurrency, False)

    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON

    provider = TracerProvider(sampler=ALWAYS_OFF if mode == "non-recording" else ALWAYS_ON)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    configure_opentelemetry(
        tracer_provider=provider, meter_provider=meter,
        tracing=mode != "metrics", metrics=mode in {"metrics", "combined"},
    )

    def reset() -> None:
        exporter.clear()
        reader.get_metrics_data()

    try:
        result = await run_client("disabled", requests, concurrency, False, after_warmup=reset)
        result["exported_spans"] = len(exporter.get_finished_spans())
        expected = requests if mode in {"recording", "combined"} else 0
        assert result["exported_spans"] == expected
        result["metric_collection_available"] = reader.get_metrics_data() is not None
        return result
    finally:
        configure_opentelemetry(enabled=False)
        provider.shutdown()
        meter.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=MODES)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--requests", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path, default=Path("/tmp/rboto-otel-benchmark.json"))
    args = parser.parse_args()
    if min(args.rounds, args.requests, args.concurrency) < 1:
        parser.error("rounds, requests and concurrency must be positive")
    if args.worker:
        result = asyncio.run(worker(args.worker, args.requests, args.concurrency))
        result["mode"] = args.worker
        print(json.dumps(result))
        return
    records: list[dict[str, object]] = []
    rng = random.Random(37)
    for iteration in range(args.rounds):
        modes = list(MODES)
        rng.shuffle(modes)
        for mode in modes:
            process = subprocess.run(
                [
                    sys.executable, __file__, "--worker", mode,
                    "--requests", str(args.requests), "--concurrency", str(args.concurrency),
                ],
                check=True, capture_output=True, text=True,
            )
            record = json.loads(process.stdout)
            record["round"] = iteration + 1
            records.append(record)
            print(f"round {iteration + 1}: {mode}", flush=True)
    medians = {
        mode: {
            field: statistics.median(
                float(str(record[field])) for record in records if record["mode"] == mode
            )
            for field in ("p50_us", "p95_us", "process_cpu_seconds", "process_peak_rss_kib")
        }
        for mode in MODES
    }
    args.output.write_text(json.dumps({
        "rounds": args.rounds, "requests": args.requests, "concurrency": args.concurrency,
        "medians": medians, "records": records,
    }, indent=2) + "\n")
    print(json.dumps(medians, indent=2))


if __name__ == "__main__":
    main()
