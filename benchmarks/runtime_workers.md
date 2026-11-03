# Runtime worker sizing

Measured on 2026-11-03 with CPython 3.12.15, Rust 1.98.1 and Linux ARM64.
Rust reported 32 available CPUs. Service wheels used the release profile:
LTO, one codegen unit and stripped symbols.

## Method

`runtime_workers.py` runs S3 downloads, SQS sends with ASCII messages, and mixed
S3/SQS batches. Each batch starts a fresh process, imports both service modules,
constructs only the clients needed for that case, and warms its connections.
Imports and construction were verified to leave the process at one thread.
Native diagnostics confirmed the requested worker count in each used extension
and no runtime in the unused extension.

The HTTP peer runs in a separate process on loopback with a 2 ms response delay.
Its CPU and RSS are excluded. These are local HTTP measurements, without TLS or
cloud service latency, not measurements of production endpoints. Both service
modules are imported in every process, so absolute RSS is not the footprint of
installing and importing only one client.

Each configuration ran five times in shuffled order with 64 measured operations
per batch and 2 or 16 warmup operations. The same binary, credentials, payloads,
checksums and peer were used for all worker counts. The 32-worker setting matches
the previous default on this host; this compares settings, not two revisions.

All 180 batches passed, with 11,520 measured operations and 1,760 warmup operations.
S3 downloads check CRC32 and byte equality; SQS responses check the message MD5.
Content checks are included in the timed operation. CPU covers all client-process
threads. Latency excludes time waiting for the application's concurrency semaphore.
RSS and total thread peaks are sampled every 2 ms; samples can miss shorter peaks.
Event-loop gaps are measured by a 1 ms ticker and include scheduler delays.

## Decision

Use `min(2, available_parallelism())` when neither explicit configuration nor
`TOKIO_WORKER_THREADS` supplies a value. In these cases, two workers retained
sequential and SQS throughput while avoiding the CPU, thread and memory cost of
32 workers. One worker reduced throughput for the large-message parallel SQS and
mixed cases. Four did not provide a consistent advantage over two.

For the mixed case, two workers per service gave 1,145 operations/s versus 1,142
with 32, and 85.4 ms versus 94.5 ms of process CPU per batch. Actual async workers
fell from 64 to 4; sampled total threads fell from 78 to 13, including blocking
workers and the Python thread. Peak RSS was 81.2 versus 103.7 MiB.

These results support a bounded default, not a universal throughput guarantee or
a memory-leak fix. Large CPU-bound workloads and different machines may benefit
from more workers. The explicit setting remains available.

## Results

Values are medians of five batches. Thread and RSS columns are medians of the
sampled per-batch peaks. Loop gap is the median of each batch's maximum gap.

| Case / concurrency | Workers per service | Operations/s | p95 latency (ms) | CPU (ms/op) | Total threads | Peak RSS (MiB) | Loop gap (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| MIXED 1 MiB / 16 | 1 | 798 | 47.71 | 1.307 | 8 | 84.7 | 2.44 |
| MIXED 1 MiB / 16 | 2 | 1145 | 21.91 | 1.334 | 13 | 81.2 | 2.40 |
| MIXED 1 MiB / 16 | 4 | 1145 | 21.83 | 1.379 | 19 | 86.8 | 2.21 |
| MIXED 1 MiB / 16 | 32 | 1142 | 21.19 | 1.477 | 78 | 103.7 | 2.41 |
| S3 4 KiB / 1 | 1 | 444 | 2.27 | 0.219 | 3 | 40.8 | 1.36 |
| S3 4 KiB / 1 | 2 | 439 | 2.29 | 0.259 | 4 | 41.0 | 1.36 |
| S3 4 KiB / 1 | 4 | 438 | 2.31 | 0.266 | 6 | 41.4 | 1.36 |
| S3 4 KiB / 1 | 32 | 437 | 2.32 | 0.271 | 34 | 41.8 | 1.36 |
| S3 1 MiB / 1 | 1 | 363 | 2.77 | 0.732 | 3 | 45.4 | 1.77 |
| S3 1 MiB / 1 | 2 | 377 | 2.83 | 0.675 | 4 | 47.2 | 1.84 |
| S3 1 MiB / 1 | 4 | 382 | 2.85 | 0.639 | 6 | 49.2 | 1.85 |
| S3 1 MiB / 1 | 32 | 377 | 2.87 | 0.675 | 34 | 49.6 | 1.85 |
| S3 1 MiB / 16 | 1 | 2715 | 8.96 | 0.434 | 4 | 63.0 | 1.79 |
| S3 1 MiB / 16 | 2 | 3390 | 6.30 | 0.506 | 7 | 62.5 | 1.61 |
| S3 1 MiB / 16 | 4 | 2916 | 7.84 | 0.627 | 12 | 66.7 | 2.73 |
| S3 1 MiB / 16 | 32 | 2357 | 9.56 | 0.861 | 46 | 80.9 | 4.37 |
| S3 8 MiB / 16 | 1 | 414 | 48.60 | 2.695 | 4 | 176.3 | 6.02 |
| S3 8 MiB / 16 | 2 | 480 | 45.07 | 3.020 | 7 | 189.0 | 14.49 |
| S3 8 MiB / 16 | 4 | 475 | 45.23 | 3.515 | 12 | 223.2 | 21.27 |
| S3 8 MiB / 16 | 32 | 412 | 56.60 | 4.712 | 45 | 325.7 | 24.30 |
| SQS 4 KiB / 1 | 1 | 452 | 2.22 | 0.152 | 3 | 39.4 | 1.35 |
| SQS 4 KiB / 1 | 2 | 448 | 2.24 | 0.189 | 4 | 39.6 | 1.36 |
| SQS 4 KiB / 1 | 4 | 447 | 2.26 | 0.189 | 6 | 39.9 | 1.36 |
| SQS 4 KiB / 1 | 32 | 446 | 2.27 | 0.198 | 34 | 40.4 | 1.36 |
| SQS 4 KiB / 16 | 1 | 4978 | 3.64 | 0.129 | 11 | 40.9 | 1.72 |
| SQS 4 KiB / 16 | 2 | 5123 | 3.30 | 0.160 | 13 | 41.6 | 1.94 |
| SQS 4 KiB / 16 | 4 | 5111 | 3.59 | 0.175 | 20 | 41.9 | 1.90 |
| SQS 4 KiB / 16 | 32 | 4814 | 3.85 | 0.224 | 49 | 44.3 | 2.00 |
| SQS 1 MiB / 1 | 1 | 198 | 5.10 | 1.772 | 3 | 43.4 | 1.96 |
| SQS 1 MiB / 1 | 2 | 197 | 5.14 | 1.809 | 4 | 44.6 | 1.98 |
| SQS 1 MiB / 1 | 4 | 197 | 5.12 | 1.807 | 6 | 44.7 | 1.96 |
| SQS 1 MiB / 1 | 32 | 197 | 5.13 | 1.814 | 34 | 47.1 | 1.97 |
| SQS 1 MiB / 16 | 1 | 475 | 46.21 | 2.029 | 5 | 72.9 | 4.41 |
| SQS 1 MiB / 16 | 2 | 629 | 30.31 | 2.030 | 9 | 73.9 | 4.25 |
| SQS 1 MiB / 16 | 4 | 628 | 29.89 | 2.181 | 13 | 73.5 | 4.26 |
| SQS 1 MiB / 16 | 32 | 635 | 28.48 | 2.188 | 41 | 85.1 | 3.26 |

## Reproduce

Install the core package, release S3 and SQS wheels from this revision, and
`psutil` in one environment. Set the baseline count to the available parallelism
reported by Rust on your machine; the script's default uses CPU affinity where
available.

```bash
python benchmarks/runtime_workers.py --rounds 5 --operations 64 \
  --baseline-workers 32 --output /tmp/runtime-workers.json
```

The JSON retains every batch, source-library hashes, worker counts and process
snapshots. The benchmark does not use an account or create remote resources.

Native library SHA-256 values for the measured builds:

- `s3`: `562033c14e745b8814b6c37da0cbf17d759c51674e7757a4df32c8675d4d37e4`
- `sqs`: `94cff5efbe58d9aa169b3317ec580ec4dc255c916e363a95fceb318fb38b1e87`
