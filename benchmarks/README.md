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

## Operation logging

The same local endpoint measures the built-in logging middleware:

```bash
.venv/bin/python benchmarks/interceptors.py --mode disabled --requests 4000
.venv/bin/python benchmarks/interceptors.py --mode middleware --requests 4000
.venv/bin/python benchmarks/interceptors.py --mode logging-disabled --requests 4000
.venv/bin/python benchmarks/interceptors.py --mode logging-info --requests 4000
.venv/bin/python benchmarks/interceptors.py --mode logging-debug --requests 4000
```

`disabled` has no middleware. `middleware` has one pass-through interceptor.
`logging-disabled` installs `LoggingInterceptor` with its logger at `CRITICAL`,
so the middleware and native metadata collection still run but emit no records.
The enabled modes use a synchronous in-memory handler that counts every record
and retains the last 256. It performs no formatting, file writes, or network I/O.
The measured operation succeeds on its first attempt, so both enabled modes
emit one completion record per call; `DEBUG` emits no retry summary in this case.

Use `--allocations` separately to measure Python traced peaks. Retained log records,
the local server, and the benchmark's latency samples are part of that peak. This
does not measure native allocations or the cost of a production log destination.

Measured on 2026-10-28 with CPython 3.12.15 on Linux ARM64, using the same
unoptimized native extensions with debug symbols disabled. Each mode ran five
times in shuffled order, with 4,000 requests and concurrency 8. The table shows
medians across runs; all modes use the same revision and native extension.

| Mode | p50 (ms) | p95 (ms) | Process CPU per call (µs) | Peak RSS (MiB) |
| --- | ---: | ---: | ---: | ---: |
| No middleware | 0.904 | 1.021 | 903.3 | 54.74 |
| Pass-through middleware | 1.143 | 1.420 | 1005.7 | 55.62 |
| Logging at CRITICAL | 1.140 | 1.420 | 1005.6 | 55.67 |
| Logging at INFO | 1.215 | 1.539 | 1019.1 | 55.53 |
| Logging at DEBUG, no retries | 1.231 | 1.570 | 1021.7 | 55.88 |

Enabling INFO added 0.075 ms to median latency (6.60%) and 1.34% to process CPU
relative to the installed but disabled logger. The difference from no middleware
also includes the chain and native metadata collection. RSS is a process peak
and varies between runs.

Separate 2,000-request allocation runs recorded Python traced peaks of 349,976
bytes without middleware, 708,666 with pass-through middleware, 707,858 with
logging disabled, 1,018,484 at INFO, and 1,019,601 at DEBUG. These include the
handler's bounded sample of 256 records and are not per-operation allocation
counts. Repeat with release wheels and the intended handler before estimating
production costs.
