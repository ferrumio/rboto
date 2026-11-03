# Shared output access: release-wheel measurements

Reading a collection no longer clones the payloads inside its native objects.
This substantially reduces getter work and memory when retaining several views.
It adds native allocations when preparing responses. **It is not a universal
request-latency improvement**, and synchronous Python conversion remains.

## Method

Measured on 2026-11-03, Linux ARM64, CPython 3.12.15, Rust 1.98.1, 32 available
CPUs, two native runtime workers. Both builds use the same locked dependencies
and release profile: LTO, one codegen unit, stripped symbols. The baseline is
`5496981`; the S3/SQS baseline wheels reuse the identical native sources from
`aa4f912`. The after wheels contain this PR's runtime changes. Final generation
also adds a Clippy-only allowance for uniform conversion closures; it changes
no runtime logic. Library hashes below identify exactly what was measured.

`output_access.py` runs every batch in a fresh process against a separate local
HTTP peer. Peer CPU and memory are excluded. Five rounds per build, shuffled
order, matched payloads, two warmup requests, no concurrent compilation during
the final measurements. There were 260 initial batches and 30 follow-up batches
that consume results once, to check the cost of preparing unused collections.
All 290 completed without failures. The peer counted 4,320 measured requests
and 580 warmup requests. Retry attempts are limited to one; no retries occurred.
Sequential connections were warmed and reused; parallel batches can open more.

Shapes: SQS has 10 messages of 128 B or 1 MiB, each with one small native message
attribute. Its 1,000-message/128 B case is a **synthetic framework stress shape**,
not a valid SQS response size. S3 listings contain 10 or 1,000 objects with owners;
stream bodies are 1 MiB. DynamoDB returns 10 nested dictionary items of 128 B or
64 KiB, and its input case sends four 64 KiB strings in a nested map. Lambda
exercises a 1 MiB blob. All text payloads are ASCII.

Getter/dictionary cases fetch a fresh response before timing its first access,
then measure 100 repeated accesses, yielding between iterations. They retain
eight results afterward to measure memory, then keep just one child and finally
release it. Request cases perform 24 operations: sequentially, or three batches
of eight. `request` discards each new result without reading its fields;
`parallel` also checks message counts. `consume` reads identifiers and nested
metadata once. S3 reads/iteration verify the complete body.

The 1 ms asyncio ticker measures observed intervals, including scheduling and
GIL contention; it does not measure GIL hold time directly. Native-entry timings
cover the direct PyO3 call until it returns an awaitable, including bridge setup.
They exclude the public Python wrapper's parameter-dictionary construction.
Process CPU includes all native threads. RSS is sampled from another process
every 1 ms after the batch is ready; short peaks may be missed. Peak RSS also
includes the later retention experiment for getter cases.

## Getter work

Values are medians across five rounds. First access and repeated-access p50 are
synchronous durations on the Python thread, independent of network latency.

| Collection | First access, µs | Repeated access, µs | Max loop interval, ms |
| --- | ---: | ---: | ---: |
| SQS: 10 × 128 B | 8.56 → 3.02 | 2.040 → 0.508 | 1.64 → 1.37 |
| SQS: 10 × 1 MiB | 2,542.93 → 4.08 | 305.765 → 0.500 | 3.96 → 1.37 |
| Synthetic: 1,000 messages | 592.79 → 73.99 | 325.941 → 22.167 | 2.41 → 1.56 |
| S3: 10 objects | 13.94 → 8.85 | 1.148 → 0.515 | 1.48 → 1.38 |
| S3: 1,000 objects | 226.46 → 83.76 | 112.183 → 21.902 | 1.62 → 1.50 |

Each cell is **before → after**. The large-message collection getter is about
600 times faster in this measurement; this is not a claim about sending or
receiving messages over the network.

## Memory and output lifetime

For 10 × 1 MiB messages, eight Python lists refer to the same native payloads in
the after build. The before build cloned all ten bodies for every list.
`mallinfo2` allocated bytes below are arena bytes in use plus allocated mmap
bytes, not a complete accounting of Python or process memory. The allocator was
not trimmed. RSS can stay high after objects are freed and is not evidence of a
leak on its own.

| State | Allocator bytes in use, MiB | RSS, MiB |
| --- | ---: | ---: |
| Eight lists plus original response | 96.74 → 16.67 | 143.38 → 63.30 |
| One child; parents and siblings released | 7.65 → 7.66 | 122.52 → 63.30 |
| All outputs released | 6.65 → 6.66 | 122.52 → 42.99 |

The approximately 80 MiB reduction while holding eight lists matches the avoided
body copies. Keeping one child retains only its subtree. Both builds converge
to similar live allocator usage after release. Frozen wrappers and fresh
containers preserve mutation isolation; output object identity is not promised.

## Costs that remain

| Path | Measurement, µs, before → after |
| --- | ---: |
| Native entry: 128 B string | 6.29 → 6.67 |
| Native entry: 1 MiB string | 33.91 → 34.29 |
| Native entry: 256 KiB nested map | 15.77 → 15.64 |
| Native entry: 1 MiB blob | 184.68 → 184.62 |
| Whole 10 MiB output: `to_dict()` | 802.42 → 801.34 |
| Nested dictionary unions: 640 KiB | 53.48 → 53.47 |
| Read 1 MiB bytes property | 19.50 → 19.71 |

These paths were not optimized here. Input conversion, scalar string/blob access,
dictionary unions and `to_dict()` still allocate/copy under the GIL. Native
workers also need the GIL to deliver Python results and create stream bytes.
Releasing the GIL alone would not free a getter's calling asyncio thread.

The idle ticker's median maximum interval was 1.07 → 1.07 ms;
the deliberate 10 ms blocking control produced 11.13 → 11.13 ms.
Logging OFF and in-memory JSON logging measured 160.25 → 161.66 µs and
201.54 → 202.47 µs per request respectively. A controlled destination
that sleeps 5 ms in `write()` raised the loop interval to 5.36 → 5.39 ms.
Logging delivery remains synchronous; the change does not hide or offload it.

## Request tradeoffs

The small 10-message response was effectively unchanged. An unread 1,000-object
S3 listing went from 2.025 to 2.142 ms (5.8% slower). Reading identifiers and
owners once measured 2.441 → 2.494 ms (about 2.2% slower).
The synthetic unread 1,000-message response increased by about 22%, with higher
CPU. Reading its identifiers and attributes once reduced elapsed time but still
used more CPU. Native node allocation and collection rebuilding have real costs.

Large-message request timings varied: sequential round medians ranged from
15.50–18.36 ms before and 16.67–18.60 ms after. Neither these local timings nor
the getter improvement establish a remote-service throughput improvement. They
do not explain previous multi-second `SendMessage` differences.

The change favors avoiding large/nested copies and retaining multiple views.
Applications that mostly discard large collections can pay extra native CPU.
No claim is made that every workload improves. The full matrix below makes the
unchanged paths and regressions visible.

## Full local matrix

p50 is repeated getter time for `getters`/`dict`, or complete operation time for
request/entry/stream/consume cases. CPU is for the whole batch, including result
release and the ticker; batch lengths differ as described above. All cells are
before → after. `small`, `large` and `wide` use the per-service shapes above.

| Service / size / mode | p50, µs | Batch CPU, ms | Max loop interval, ms | Peak RSS, MiB |
| --- | ---: | ---: | ---: | ---: |
| dynamodb / large / entry | 875.30 → 873.07 | 13.71 → 13.61 | 1.83 → 1.83 | 37.90 → 37.76 |
| dynamodb / large / getters | 53.48 → 53.47 | 6.15 → 6.16 | 1.37 → 1.37 | 43.35 → 43.01 |
| dynamodb / small / getters | 4.28 → 4.28 | 0.85 → 0.85 | 1.88 → 1.88 | 37.18 → 37.17 |
| lambda / large / entry | 2,133.86 → 2,221.47 | 48.11 → 49.05 | 2.01 → 2.00 | 49.20 → 47.41 |
| lambda / large / getters | 19.50 → 19.71 | 2.58 → 2.59 | 1.31 → 1.31 | 50.47 → 50.63 |
| s3 / large / chunks | 971.36 → 966.93 | 26.12 → 26.17 | 1.64 → 1.63 | 45.67 → 45.48 |
| s3 / large / read | 449.28 → 457.83 | 11.64 → 11.98 | 1.68 → 1.90 | 44.73 → 44.44 |
| s3 / small / getters | 1.15 → 0.52 | 0.44 → 0.33 | 1.48 → 1.38 | 39.14 → 39.15 |
| s3 / wide / consume | 2,441.04 → 2,493.92 | 63.98 → 66.52 | 1.80 → 1.82 | 40.76 → 41.14 |
| s3 / wide / getters | 112.18 → 21.90 | 22.07 → 6.50 | 1.62 → 1.50 | 42.48 → 40.52 |
| s3 / wide / request | 2,025.17 → 2,141.87 | 52.18 → 58.28 | 2.05 → 1.45 | 40.80 → 40.88 |
| sqs / large / consume | 18,435.13 → 17,962.75 | 430.79 → 418.29 | 4.05 → 2.72 | 97.83 → 88.20 |
| sqs / large / dict | 802.42 → 801.34 | 84.72 → 84.59 | 4.97 → 4.97 | 141.81 → 141.14 |
| sqs / large / entry | 4,652.69 → 4,659.14 | 42.86 → 42.85 | 1.60 → 1.54 | 38.41 → 38.33 |
| sqs / large / getters | 305.77 → 0.50 | 34.50 → 0.32 | 3.96 → 1.37 | 143.36 → 63.30 |
| sqs / large / parallel | 51,687.24 → 52,661.31 | 408.22 → 404.24 | 7.74 → 4.29 | 179.12 → 173.56 |
| sqs / large / request | 17,586.98 → 18,077.93 | 409.73 → 429.03 | 2.44 → 2.67 | 88.36 → 88.13 |
| sqs / small / blocking | — | 0.41 → 0.39 | 11.13 → 11.13 | 34.40 → 34.41 |
| sqs / small / entry | 144.17 → 145.24 | 3.42 → 3.50 | 1.48 → 1.48 | 34.41 → 34.43 |
| sqs / small / getters | 2.04 → 0.51 | 0.60 → 0.32 | 1.64 → 1.37 | 34.41 → 34.54 |
| sqs / small / idle | — | 0.29 → 0.29 | 1.07 → 1.07 | 34.49 → 34.56 |
| sqs / small / logging | 201.54 → 202.47 | 5.12 → 5.11 | 1.42 → 1.49 | 34.84 → 34.77 |
| sqs / small / logging-off | 160.25 → 161.66 | 4.08 → 4.23 | 1.69 → 1.86 | 34.49 → 34.51 |
| sqs / small / logging-slow | 5,257.21 → 5,259.88 | 5.36 → 5.39 | 5.36 → 5.39 | 34.75 → 34.85 |
| sqs / small / parallel | 505.97 → 525.71 | 5.24 → 5.11 | 1.34 → 1.31 | 35.03 → 35.06 |
| sqs / small / request | 161.21 → 161.15 | 4.13 → 4.24 | 1.83 → 1.83 | 34.50 → 34.45 |
| sqs / wide / consume | 2,353.24 → 2,267.24 | 69.60 → 77.75 | 2.64 → 3.44 | 41.14 → 40.12 |
| sqs / wide / getters | 325.94 → 22.17 | 54.00 → 6.58 | 2.41 → 1.56 | 47.63 → 37.09 |
| sqs / wide / request | 1,646.74 → 2,015.01 | 49.69 → 70.25 | 2.33 → 3.21 | 40.04 → 40.09 |

## Live service confirmation

`output_access_aws.py` passed 12 isolated SQS checks in `us-east-1`: three rounds
per build for 4 KiB and 1 MiB ASCII messages. It checked content, MD5, message
identity, repeated access, dictionary conversion and a child's lifetime after
releasing its parent. Every message was deleted; the UUID queue was deleted
and a separate `GetQueueUrl` confirmed its absence. This was a functional
confirmation, not a controlled remote request-latency comparison.

Compiled local tests additionally cover optional/empty collections, unknown enum
keys, independent mutable containers, multi-threaded getters, nested lifetimes,
modeled errors, cancellation and single-consumption streams. A standalone
compiled generator fixture checks payload addresses and sibling deallocation,
including nested boxes/options and maps with unusual Rust type formatting.

## Reproduce

Build both revisions with the same toolchain using `maturin build --release
--locked` from each service package directory. Extract the four service wheels
into separate before/after directories. Install the core package and `psutil`
in the runner's Python environment. The live check also needs the AWS CLI and
permission to manage its temporary queue.

```bash
python benchmarks/output_access.py --before-path /tmp/before --after-path /tmp/after \
  --rounds 5 --output /tmp/output-access.json
python benchmarks/output_access_aws.py --before-path /tmp/before --after-path /tmp/after \
  --rounds 3 --region us-east-1 --output /tmp/output-access-aws.json
```

| Service | Before native SHA-256 | After native SHA-256 |
| --- | --- | --- |
| s3 | `562033c14e745b8814b6c37da0cbf17d759c51674e7757a4df32c8675d4d37e4` | `4d5bb6bc6d3305ee1291af85b56cbd213e8d7bb580c9ab206190b290df7fd6eb` |
| sqs | `94cff5efbe58d9aa169b3317ec580ec4dc255c916e363a95fceb318fb38b1e87` | `4ea9754948552a18b18109fa4bc7fa0435e724c2576ab2522e8e31b068ea43c5` |
| dynamodb | `05f40b4ed5a342a3564c69d6e1ae17da570a2af1ee72f9f8e3a1f9430cb90bc4` | `f9e3b02f16a0f19842c4f8e0725328fdf30871c220ead923ef94e9cebeb61392` |
| lambda | `ecfc8bff70cdf35d832869d661078b9dc6a43052e9eff9fe364b84df69012c19` | `97abff64f98cb7c5e06f3c36eb4765fb695d763a03ab18ab0a1ac357eba04e57` |

The wheels did not grow substantially in this Linux ARM64/CPython 3.12 build:

| Package | Compressed wheel, MiB | Native library, MiB |
| --- | ---: | ---: |
| rboto-dynamodb | 5.496 → 5.477 | 12.833 → 12.770 |
| rboto-lambda | 6.021 → 6.058 | 14.525 → 14.525 |
| rboto-s3 | 7.390 → 7.385 | 17.799 → 17.736 |
| rboto-sqs | 4.180 → 4.180 | 9.009 → 9.009 |
