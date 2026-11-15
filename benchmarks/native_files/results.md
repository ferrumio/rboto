# Native file streaming measurements

Measured on 2026-11-15 at source revision `24f74798967fd6eea3926f68a00ee3c6c6b13352`:
CPython 3.12.15, Linux ARM64, 32 logical CPUs, Rust 1.98.1. The S3 extension used
optimization level 3, LTO and one codegen unit. Every mode used the same wheel.

The complete matrix ran 360 fresh worker processes, five randomized rounds per
configuration, and 900 measured transfers (50,646,220,800 bytes). Every transfer
passed its SHA-256 content check. Runtime/connection warmup and download verification
were outside timing. See the [methodology](README.md),
[environment and wheel digest](environment-2026-11-15.json), and
[compressed raw measurements](measurements-2026-11-15.jsonl.gz).

## Four simultaneous 128 MiB files

Values are medians across five runs. CPU is process user plus system time, including
native and Python worker threads and excluding the separate HTTP peer.

| Backend | Direction | Python wall s | Native wall s | Python CPU s | Native CPU s | CPU reduction |
|---|---|---:|---:|---:|---:|---:|
| asyncio | upload | 0.5840 | 0.5540 | 0.2220 | 0.1195 | 46.2% |
| asyncio | download | 0.1800 | 0.0857 | 0.3290 | 0.1445 | 56.1% |
| trio | upload | 0.5821 | 0.5816 | 0.2353 | 0.1208 | 48.6% |
| trio | download | 0.2205 | 0.0858 | 0.3861 | 0.1422 | 63.2% |

The native download reduced elapsed time by 52.4% in asyncio and 61.1% in Trio.
Upload elapsed time changed much less: 5.1% in asyncio and 0.1% in Trio. The clear
upload improvement was lower CPU use. The Python control that batches output
chunks into roughly 1 MiB writes was slower than either download path here.

## Memory and scheduler behavior

The bytes control preloads one immutable Python payload, shared across simultaneous
calls. Loading is excluded from time; absolute RSS includes the payload and the
native copies made by each call. Python streaming already avoids loading the whole
file. FileBody removes Python chunk handling as well.

Peak process RSS for four uploads in asyncio, in MiB:

| File size | Preloaded bytes | Python streaming | FileBody |
|---|---:|---:|---:|
| 1 MiB | 45.4 | 46.4 | 41.5 |
| 32 MiB | 201.2 | 56.4 | 53.4 |
| 128 MiB | 681.3 | 58.4 | 53.4 |

For 128 MiB × 4 uploads in asyncio, the median of each run's largest heartbeat gap
was 132.1 ms with preloaded bytes, 2.06 ms with Python streaming and 1.67 ms with
FileBody. A percentile alone can hide the single large buffer-copy stall.

Download peak RSS was essentially equal: about 51.4 MiB for both Python and native
paths in asyncio. Download heartbeat gaps did not improve: the corresponding
medians of maximum gaps were 1.55 ms and 1.87 ms. These gaps include scheduler
jitter and do not directly measure GIL holding time. There is no general claim
of lower memory or shorter loop gaps for every operation.

## Scope and validation

This is a loopback HTTP experiment with fake credentials, warm filesystem page
cache and no fsync. It does not measure real AWS throughput, TLS, physical disk
speed or crash durability. Optional SDK checksums were disabled equally for
performance; compiled functional tests separately verified CRC32 trailers.
Hardware CPU cycles were not measured because perf was unavailable.

The broad integration/core suite passed 588 tests with 124 skips; that run selected
six installed services.
The focused file suite passed 44 tests across asyncio and Trio, including retries,
changed sources, cancellation, ownership, destination failures and truncated
responses. Protocol checks covered S3, Lambda file inputs, Polly response streams
and unchanged Bedrock ordinary blobs. S3 file round-trips also passed on LocalStack.

The full catalog was regenerated. Rust workspace tests and Clippy, formatting,
codegen/core unit tests, strict mypy (390 source files), Pyright and the strict
documentation build passed. Release-platform coverage remains the responsibility
of CI; local native execution used CPython 3.12 on Linux ARM64.

## Complete matrix

All columns are medians across five runs, including the per-run maximum gap.
RSS was sampled every 5 ms. Short transfers can have too few heartbeat samples
for useful percentiles. The native and Python file buffers were 1 MiB.

| Backend | Direction | MiB × concurrency | Mode | Wall s | CPU s | Peak RSS MiB | RSS growth MiB | Loop p99 ms | Max gap ms |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| asyncio | download | 1 × 1 | native | 0.0017 | 0.0018 | 40.0 | 0.0 | 1.58 | 1.58 |
| asyncio | download | 1 × 1 | python | 0.0022 | 0.0028 | 40.0 | 0.0 | 1.13 | 1.13 |
| asyncio | download | 1 × 1 | python-batched | 0.0023 | 0.0029 | 40.0 | 0.0 | 1.34 | 1.34 |
| asyncio | download | 1 × 4 | native | 0.0031 | 0.0064 | 40.0 | 0.0 | 1.88 | 1.88 |
| asyncio | download | 1 × 4 | python | 0.0074 | 0.0121 | 43.3 | 3.2 | 1.71 | 1.71 |
| asyncio | download | 1 × 4 | python-batched | 0.0078 | 0.0121 | 45.2 | 5.2 | 1.52 | 1.52 |
| asyncio | download | 32 × 1 | native | 0.0057 | 0.0085 | 42.6 | 2.6 | 1.92 | 1.92 |
| asyncio | download | 32 × 1 | python | 0.0129 | 0.0185 | 42.8 | 2.8 | 1.61 | 1.61 |
| asyncio | download | 32 × 1 | python-batched | 0.0184 | 0.0237 | 43.3 | 3.3 | 1.57 | 1.57 |
| asyncio | download | 32 × 4 | native | 0.0208 | 0.0382 | 50.9 | 10.8 | 1.89 | 1.89 |
| asyncio | download | 32 × 4 | python | 0.0505 | 0.0913 | 50.8 | 10.7 | 1.75 | 1.75 |
| asyncio | download | 32 × 4 | python-batched | 0.0750 | 0.1121 | 52.3 | 12.3 | 1.88 | 1.88 |
| asyncio | download | 128 × 1 | native | 0.0185 | 0.0303 | 43.8 | 3.8 | 1.69 | 1.69 |
| asyncio | download | 128 × 1 | python | 0.0436 | 0.0646 | 43.2 | 3.2 | 1.86 | 1.86 |
| asyncio | download | 128 × 1 | python-batched | 0.0649 | 0.0852 | 43.7 | 3.7 | 1.56 | 1.56 |
| asyncio | download | 128 × 4 | native | 0.0857 | 0.1445 | 51.4 | 11.4 | 1.87 | 1.87 |
| asyncio | download | 128 × 4 | python | 0.1800 | 0.3290 | 51.4 | 11.4 | 1.43 | 1.55 |
| asyncio | download | 128 × 4 | python-batched | 0.2810 | 0.4190 | 52.5 | 12.4 | 1.83 | 2.00 |
| asyncio | upload | 1 × 1 | bytes | 0.0030 | 0.0018 | 41.0 | 0.0 | 1.52 | 1.52 |
| asyncio | upload | 1 × 1 | native | 0.0024 | 0.0016 | 40.0 | 0.0 | 1.21 | 1.21 |
| asyncio | upload | 1 × 1 | python | 0.0028 | 0.0020 | 40.1 | 0.0 | 2.05 | 2.05 |
| asyncio | upload | 1 × 4 | bytes | 0.0069 | 0.0064 | 45.4 | 4.3 | 1.64 | 1.64 |
| asyncio | upload | 1 × 4 | native | 0.0065 | 0.0062 | 41.5 | 1.4 | 1.77 | 1.77 |
| asyncio | upload | 1 × 4 | python | 0.0069 | 0.0070 | 46.4 | 6.4 | 1.76 | 1.76 |
| asyncio | upload | 32 × 1 | bytes | 0.0645 | 0.0383 | 104.5 | 32.5 | 9.06 | 9.06 |
| asyncio | upload | 32 × 1 | native | 0.0320 | 0.0084 | 43.6 | 3.5 | 1.89 | 1.89 |
| asyncio | upload | 32 × 1 | python | 0.0319 | 0.0116 | 44.5 | 4.5 | 2.02 | 2.02 |
| asyncio | upload | 32 × 4 | bytes | 0.1962 | 0.1524 | 201.2 | 129.2 | 1.86 | 35.83 |
| asyncio | upload | 32 × 4 | native | 0.1455 | 0.0359 | 53.4 | 13.4 | 1.36 | 1.78 |
| asyncio | upload | 32 × 4 | python | 0.1403 | 0.0612 | 56.4 | 16.4 | 2.01 | 2.03 |
| asyncio | upload | 128 × 1 | bytes | 0.2546 | 0.1503 | 296.5 | 128.5 | 1.09 | 35.60 |
| asyncio | upload | 128 × 1 | native | 0.1218 | 0.0244 | 44.5 | 4.5 | 1.20 | 1.79 |
| asyncio | upload | 128 × 1 | python | 0.1215 | 0.0369 | 44.7 | 4.6 | 1.93 | 1.93 |
| asyncio | upload | 128 × 4 | bytes | 0.7935 | 0.5871 | 681.3 | 513.2 | 1.07 | 132.12 |
| asyncio | upload | 128 × 4 | native | 0.5540 | 0.1195 | 53.4 | 13.4 | 1.08 | 1.67 |
| asyncio | upload | 128 × 4 | python | 0.5840 | 0.2220 | 58.4 | 18.4 | 2.02 | 2.06 |
| trio | download | 1 × 1 | native | 0.0018 | 0.0020 | 43.8 | 0.0 | 1.59 | 1.59 |
| trio | download | 1 × 1 | python | 0.0025 | 0.0032 | 43.8 | 0.0 | 1.41 | 1.41 |
| trio | download | 1 × 1 | python-batched | 0.0026 | 0.0033 | 43.9 | 0.0 | 1.49 | 1.49 |
| trio | download | 1 × 4 | native | 0.0031 | 0.0065 | 43.8 | 0.0 | 1.76 | 1.76 |
| trio | download | 1 × 4 | python | 0.0089 | 0.0141 | 46.1 | 2.3 | 1.50 | 1.50 |
| trio | download | 1 × 4 | python-batched | 0.0092 | 0.0138 | 47.7 | 3.8 | 1.74 | 1.74 |
| trio | download | 32 × 1 | native | 0.0064 | 0.0098 | 46.8 | 3.0 | 1.85 | 1.85 |
| trio | download | 32 × 1 | python | 0.0146 | 0.0203 | 46.1 | 2.3 | 1.37 | 1.37 |
| trio | download | 32 × 1 | python-batched | 0.0195 | 0.0253 | 46.6 | 2.8 | 1.61 | 1.61 |
| trio | download | 32 × 4 | native | 0.0219 | 0.0394 | 54.1 | 10.3 | 1.84 | 1.84 |
| trio | download | 32 × 4 | python | 0.0610 | 0.1058 | 54.5 | 10.7 | 1.73 | 1.73 |
| trio | download | 32 × 4 | python-batched | 0.0818 | 0.1213 | 54.9 | 11.2 | 1.69 | 1.69 |
| trio | download | 128 × 1 | native | 0.0216 | 0.0342 | 47.3 | 3.5 | 1.63 | 1.63 |
| trio | download | 128 × 1 | python | 0.0505 | 0.0726 | 46.0 | 2.2 | 1.59 | 1.59 |
| trio | download | 128 × 1 | python-batched | 0.0701 | 0.0916 | 46.6 | 2.8 | 1.37 | 1.37 |
| trio | download | 128 × 4 | native | 0.0858 | 0.1422 | 54.9 | 11.1 | 1.87 | 1.87 |
| trio | download | 128 × 4 | python | 0.2205 | 0.3861 | 55.2 | 11.4 | 1.40 | 1.67 |
| trio | download | 128 × 4 | python-batched | 0.3076 | 0.4539 | 55.9 | 12.2 | 1.62 | 1.89 |
| trio | upload | 1 × 1 | bytes | 0.0029 | 0.0018 | 44.8 | 0.0 | 1.49 | 1.49 |
| trio | upload | 1 × 1 | native | 0.0024 | 0.0016 | 43.9 | 0.0 | 1.25 | 1.25 |
| trio | upload | 1 × 1 | python | 0.0027 | 0.0020 | 43.8 | 0.0 | 2.02 | 2.02 |
| trio | upload | 1 × 4 | bytes | 0.0070 | 0.0065 | 48.3 | 3.5 | 1.64 | 1.64 |
| trio | upload | 1 × 4 | native | 0.0062 | 0.0061 | 44.6 | 0.8 | 1.69 | 1.69 |
| trio | upload | 1 × 4 | python | 0.0068 | 0.0070 | 49.3 | 5.5 | 1.28 | 1.28 |
| trio | upload | 32 × 1 | bytes | 0.0645 | 0.0387 | 108.1 | 32.3 | 9.14 | 9.14 |
| trio | upload | 32 × 1 | native | 0.0325 | 0.0080 | 47.3 | 3.5 | 1.58 | 1.58 |
| trio | upload | 32 × 1 | python | 0.0322 | 0.0130 | 48.0 | 4.2 | 1.94 | 1.94 |
| trio | upload | 32 × 4 | bytes | 0.1958 | 0.1545 | 204.3 | 128.5 | 2.05 | 36.40 |
| trio | upload | 32 × 4 | native | 0.1453 | 0.0373 | 56.7 | 12.8 | 1.50 | 1.69 |
| trio | upload | 32 × 4 | python | 0.1467 | 0.0707 | 61.3 | 17.5 | 2.01 | 2.02 |
| trio | upload | 128 × 1 | bytes | 0.2543 | 0.1518 | 300.1 | 128.3 | 1.12 | 35.43 |
| trio | upload | 128 × 1 | native | 0.1218 | 0.0255 | 47.3 | 3.5 | 1.24 | 1.73 |
| trio | upload | 128 × 1 | python | 0.1215 | 0.0409 | 48.1 | 4.3 | 2.03 | 2.03 |
| trio | upload | 128 × 4 | bytes | 0.7733 | 0.5915 | 684.3 | 512.5 | 1.11 | 132.43 |
| trio | upload | 128 × 4 | native | 0.5816 | 0.1208 | 57.7 | 13.8 | 1.10 | 1.70 |
| trio | upload | 128 × 4 | python | 0.5821 | 0.2353 | 60.6 | 16.8 | 2.03 | 2.07 |
