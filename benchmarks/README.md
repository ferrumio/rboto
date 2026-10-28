# Interceptor overhead

`interceptors.py` runs a compiled DynamoDB client against an HTTP server on
loopback. It supplies test credentials and does not contact an external service.
Run each mode in a fresh process:

```bash
.venv/bin/python benchmarks/interceptors.py --mode disabled
.venv/bin/python benchmarks/interceptors.py --mode sync
.venv/bin/python benchmarks/interceptors.py --mode async
.venv/bin/python benchmarks/interceptors.py --mode sync --allocations
```

The `baseline` mode omits the interceptor constructor argument. Run it with the
parent revision's environment and native extension, passing the same script.
Warmup and connection shutdown are outside the measured interval.

The server shares the client's process, so CPU and RSS include both. Latency is
measured per call; process CPU includes all native threads. The allocation mode
reports Python traced allocations separately and does not measure native heap
allocations.

## Initial comparison

Measured on 2026-10-28 with CPython 3.12.15, Linux ARM64, and unoptimized native
extensions with debug symbols disabled. The baseline is `8ced8d2`, included in
the parent revision `3fa8411`. Each mode ran five times in alternating order,
with 4,000 measured requests per run and concurrency 8. Values below are medians
across runs.

| Mode | p50 (ms) | p95 (ms) | Process CPU per call (µs) | Peak RSS (MiB) |
| --- | ---: | ---: | ---: | ---: |
| Parent revision | 0.880 | 0.990 | 884.9 | 53.52 |
| Interceptors disabled | 0.887 | 0.999 | 894.6 | 54.48 |
| Empty sync callback | 1.097 | 1.301 | 994.6 | 54.98 |
| Empty async callback | 1.092 | 1.302 | 997.3 | 55.22 |

The disabled path added 0.75% to median latency and 1.09% to process CPU in
this run. Observation with an empty sync callback added 24.59% to median latency
and 12.39% to process CPU. Real callbacks add their own work.

Separate allocation runs used 2,000 requests with concurrency 8. Python traced
peaks were 366,110 bytes for the parent, 349,912 bytes with interception disabled,
443,675 bytes with a sync callback, and 441,077 bytes with an async callback.
These are peaks for the whole measured run, not bytes allocated per operation.

Repeat with release wheels before using these development measurements to
estimate production overhead. Remote service latency and exporter or handler
I/O are not represented here.
