# Interceptor overhead

rboto is an independent project maintained by [Ferrumio](https://github.com/ferrumio).
It is not affiliated with, sponsored by, or endorsed by Amazon Web Services (AWS).

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

## Global JSON logging

The same local endpoint measures automatic logging configured through
`configure_logging()`:

```bash
.venv/bin/python benchmarks/interceptors.py --mode disabled --requests 4000
.venv/bin/python benchmarks/interceptors.py --mode middleware --requests 4000
.venv/bin/python benchmarks/interceptors.py --mode logging-disabled --requests 4000
.venv/bin/python benchmarks/interceptors.py --mode logging-info --requests 4000
.venv/bin/python benchmarks/interceptors.py --mode logging-debug --requests 4000
```

`disabled` leaves global logging unconfigured. `middleware` adds one pass-through
application interceptor. `logging-disabled` configures level OFF and bypasses the
automatic middleware and its native metadata collection. The enabled modes include
the built-in JSON formatter and handler. Their text stream counts output lines and
retains the last 256 without external I/O. Each measured request succeeds on its
first attempt, so DEBUG adds no retry records in this scenario.

Warmup and connection shutdown are outside the measured interval. CPU and RSS
include the local HTTP peer. Allocation runs include retained JSON strings and
latency samples; they exclude native allocations and are not per-call counts.

Measured on 2026-10-28 with CPython 3.12.15 on Linux ARM64 and unoptimized native
extensions with debug symbols disabled. Each mode ran five times in shuffled
order, with 4,000 requests and concurrency 8. Values are medians across runs.

| Mode | p50 (ms) | p95 (ms) | Process CPU per call (µs) | Peak RSS (MiB) |
| --- | ---: | ---: | ---: | ---: |
| Logging unconfigured | 0.906 | 1.017 | 903.1 | 54.88 |
| Pass-through application middleware | 1.132 | 1.407 | 1002.6 | 55.13 |
| Global logging OFF | 0.910 | 1.030 | 903.8 | 54.78 |
| Global JSON logging INFO | 1.325 | 1.644 | 1040.4 | 56.16 |
| Global JSON logging DEBUG, no retries | 1.325 | 1.659 | 1038.8 | 55.72 |

The INFO path includes automatic middleware, native metadata collection, JSON
serialization, and a synchronous write to the memory stream. Compared with OFF,
it added 0.415 ms to median latency (45.65%) and 15.12% to process CPU in this run.
Compared with pass-through middleware, its median latency was 0.193 ms higher.
OFF bypassed the metadata collection path and measured close to unconfigured
logging. Process RSS varies between runs.

Separate 2,000-request allocation runs recorded traced Python peaks of 367,605
bytes unconfigured, 709,525 with pass-through middleware, 354,253 with logging OFF,
893,242 with INFO, and 896,250 with DEBUG. These include the last 256 serialized
JSON lines. Repeat with release wheels and the intended destination before
estimating production cost.

## Full-body stream reads

`bytestream_read.py` compares two release builds against real S3. Build both
revisions with the same Python, Rust toolchain, target, and release profile,
then extract each service wheel into a separate directory. The runner needs
`rboto`, `rboto-s3`, `boto3`, and `psutil` installed, plus credentials that can
create and delete the temporary bucket and its objects.

```bash
python benchmarks/bytestream_read.py \
  --before-path /path/to/extracted-before-wheel \
  --after-path /path/to/extracted-after-wheel \
  --region us-east-1 \
  --output /path/to/results.json
```

Each case starts a fresh process and warms its connections before measuring
12 complete downloads. The cases use 1 MiB and 8 MiB objects, sequentially and
at concurrency 12. Build order alternates across three rounds. Both builds use
the same objects, endpoint configuration, CRC32 validation, and retry policy.
Every warmup and measured body also passes length and SHA-256 checks.

The JSON includes elapsed time, process CPU, RSS sampled every millisecond,
and memory with all results held and after releasing them. CPU covers all
native threads. SHA-256 verification and memory diagnostics are outside the
timed interval. On glibc, allocator snapshots distinguish free arena space
from allocations still in use; free arena space can remain resident. The
benchmark does not trim the allocator or change the runtime's thread count.

The script creates one private UUID bucket and removes its two fixtures and
the bucket in `finally`. It records native library hashes to identify the
builds. Network timing and sampled RSS peaks vary between runs; inspect the
individual cases before drawing conclusions from their medians.

### Read comparison, 2026-10-31

Before/after values below are medians of three rounds, with 12 measured
downloads per case. Both S3 wheels were built locally using CPython 3.13.16,
Rust 1.98.1, and the same release profile (LTO, one codegen unit, stripped).
The host was Linux ARM64 with 32 CPUs; the bucket was in `us-east-1`.
The baseline was `a07f76b`, whose runtime code matches the parent of this
change. The after build uses the shared segment-to-Python-bytes helper.

| Body / concurrency | Elapsed (s) | Process CPU (ms) | Peak RSS (MiB) | RSS after release (MiB) |
| --- | ---: | ---: | ---: | ---: |
| 1 MiB / 1 | 1.351 → 1.307 | 26.8 → 24.2 | 62.7 → 59.3 | 52.0 → 49.0 |
| 1 MiB / 12 | 0.186 → 0.189 | 31.4 → 30.3 | 84.2 → 80.0 | 69.3 → 63.1 |
| 8 MiB / 1 | 3.098 → 3.146 | 156.0 → 149.6 | 192.7 → 166.1 | 80.2 → 78.3 |
| 8 MiB / 12 | 0.476 → 0.494 | 187.1 → 170.0 | 339.1 → 287.1 | 182.3 → 213.8 |

The change reduced measured CPU and peak RSS in these cases. Elapsed time did
not improve consistently. At 8 MiB/concurrency 12, RSS after releasing the
results increased by 17.3%, despite the 15.3% lower peak. In that case, median
free glibc arena space increased from 202.3 to 252.3 MiB, while arena space
not marked free stayed near 7.1 MiB. These allocator counters do not include
all Python or process mappings and are not equivalent to RSS. Removing the
intermediate copy does not guarantee lower retained memory.

All 288 measured downloads and 156 warmup downloads passed length and
SHA-256 validation. The two objects and temporary bucket were deleted, with
an independent `HeadBucket` request confirming HTTP 404 afterward.
