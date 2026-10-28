# Interceptor overhead

`interceptors.py` runs a compiled DynamoDB client against an HTTP server on
loopback. It supplies test credentials and does not contact an external service.
Run each mode in a fresh process:

```bash
.venv/bin/python benchmarks/interceptors.py --mode disabled
.venv/bin/python benchmarks/interceptors.py --mode middleware
.venv/bin/python benchmarks/interceptors.py --mode middleware --allocations
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
the parent revision `3fa8411`. Each mode ran five times in shuffled order,
with 4,000 measured requests per run and concurrency 8. Values below are medians
across runs.

| Mode | p50 (ms) | p95 (ms) | Process CPU per call (µs) | Peak RSS (MiB) |
| --- | ---: | ---: | ---: | ---: |
| Parent revision | 0.881 | 0.997 | 884.3 | 53.31 |
| Interceptors disabled | 0.900 | 1.018 | 900.3 | 54.76 |
| Pass-through middleware | 1.129 | 1.412 | 1005.0 | 55.71 |

The disabled path measured 2.22% higher median latency and 1.81% higher process
CPU than the parent in this run. One middleware that only awaits `call_next`
measured 28.11% higher median latency and 13.65% higher process CPU. This includes
the middleware chain and native attempt metadata collection. Application
middleware adds its own work.

Separate allocation runs used 2,000 requests with concurrency 8. Python traced
peaks were 359,401 bytes for the parent, 373,369 bytes with interception disabled,
and 709,664 bytes with pass-through middleware.
These are peaks for the whole measured run, not bytes allocated per operation.

Repeat with release wheels before using these development measurements to
estimate production overhead. Remote service latency and exporter or handler
I/O are not represented here.
