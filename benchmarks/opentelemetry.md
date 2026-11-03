# OpenTelemetry overhead

Measured on 2026-11-03 with CPython 3.12.15, Rust 1.99.0, Linux ARM64,
OpenTelemetry API/SDK 1.45.2, and an unoptimized DynamoDB extension with debug
symbols disabled. The native code is unchanged from the parent revision
`a3f69a5f`.

Run:

```bash
.venv/bin/python benchmarks/otel_overhead.py \
  --rounds 5 --requests 2000 --concurrency 8 \
  --output /tmp/rboto-otel-benchmark.json
```

Each mode runs in a fresh process. Mode order is shuffled with seed 37 in each
round. A compiled client calls a loopback HTTP peer using test credentials,
warming 20 calls per worker before timing. Every result is checked. Exporter
contents are cleared after warmup. The 35 measured runs completed 70,000 calls.
No external service or telemetry backend is contacted.

## Results

Values are medians of five runs. CPU includes every thread in the process and
the local HTTP peer. RSS is the peak for the complete process, including
imports, warmup, native buffers, and retained spans.

| Mode | p50 (ms) | p95 (ms) | CPU per call (µs) | Peak RSS (MiB) |
| --- | ---: | ---: | ---: | ---: |
| Not configured | 2.772 | 4.748 | 841.6 | 52.73 |
| Explicitly disabled | 2.710 | 4.813 | 833.4 | 53.48 |
| Pass-through application middleware | 2.953 | 5.321 | 923.3 | 52.96 |
| Unsampled spans, metrics off | 2.982 | 5.299 | 937.0 | 55.49 |
| Recorded spans, metrics off | 3.123 | 5.245 | 965.7 | 61.79 |
| Metrics only | 3.058 | 5.241 | 944.1 | 55.61 |
| Recorded spans and metrics | 3.223 | 5.284 | 1001.4 | 61.98 |

The disabled mode imports the optional integration and configures
`enabled=False`; the unconfigured mode never imports OpenTelemetry. Their
measured latency and CPU were close. This is not a zero-overhead guarantee.

Enabled modes include the shared middleware chain and native metadata
collection. The pass-through middleware row helps separate those costs from
OpenTelemetry. Recorded spans added about 42 µs of CPU per call above that
row, or 124 µs above unconfigured dispatch. Relative to unconfigured dispatch,
recording increased CPU by approximately 15%, and recording plus metrics by
approximately 19% in this workload.

The unsampled mode uses `ALWAYS_OFF`; no spans reach the exporter. Recorded
modes use `ALWAYS_ON` and `SimpleSpanProcessor` with `InMemorySpanExporter`.
The script checks that exactly 2,000 measured spans reach the exporter in each
recorded run. Metrics use `InMemoryMetricReader`; collection occurs outside
the timed interval.

## Limits

This measures SDK recording and in-memory span export, not network exporter
latency, console output, batching, collector load, or backend ingestion. RSS
in recorded modes includes all 2,000 retained spans. A production exporter has
different buffering and processing costs.

These are development native builds and a shared-process HTTP peer. Repeat
with release wheels, the application's concurrency and response sizes, and
the intended processors/exporters before estimating production overhead.
Small latency differences between modes are subject to scheduling variation.

OpenTelemetry calls are synchronous Python work around an awaited operation.
Native request I/O and retry waits remain asynchronous. Instrumentation and
application-provided synchronous processors can occupy the event-loop thread.
Later streaming-body consumption is outside the measured operation span.
