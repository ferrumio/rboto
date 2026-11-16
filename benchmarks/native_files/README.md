# Native file streaming

rboto is an independent project maintained by Ferrumio. It is not affiliated
with, sponsored by, or endorsed by Amazon Web Services (AWS).

This benchmark compares `FileBody` and `ByteStream.write_to()` with Python file
producers/writers through the same compiled S3 client. It also measures a Python
writer that batches chunks into approximately 1 MiB writes.

See the [recorded measurements](results.md) for results and compressed raw data.
Use `gzip -dc measurements-2026-11-15.jsonl.gz` to inspect the raw JSON lines.

Build the S3 package from the revision under test using the release profile.
Install the matching rboto facade, plus `anyio`, `trio`, `aiohttp` and `psutil`
in the interpreter used below. Do not compare optimized native code with debug
builds.

```bash
python benchmarks/native_files/run.py \
  --data-dir /path/on/disk \
  --output /tmp/rboto-file-results \
  --rounds 5

python benchmarks/native_files/summarize.py \
  /tmp/rboto-file-results/results.jsonl
```

The default matrix uses 1, 32 and 128 MiB files, one and four simultaneous
transfers, and asyncio/Trio. It runs 360 fresh worker processes and checks the
content of 900 transfers. `--sizes-mib 128 --backends asyncio --rounds 3` gives
a smaller comparison. Use a new output directory for each run.

The controller starts a separate local HTTP peer, uses fake credentials and
cleans its temporary data directory and peer process in `finally`. It makes
no real AWS requests. Keep `--data-dir` on a disk filesystem if `/tmp` is tmpfs.

## Measurements

- Randomized order with a fixed seed; same release extension for every mode.
- Runtime/connection warmup and one Python file read precede timing.
- The `bytes` upload control preloads one immutable payload and shares it across
  concurrent calls. Its file read is excluded from timing; absolute RSS includes
  the payload. Each native operation still copies that Python buffer.
- Wall time covers each batch through write-buffer flush or upload response.
- Process user/system CPU includes native and Python workers, excluding the peer.
- RSS is sampled every 5 ms, reporting absolute peak and growth from the
  post-warmup baseline. Brief allocation peaks can be missed.
- A 1 ms async heartbeat records scheduler gaps. These include scheduling jitter
  and are not a measurement of GIL holding time. Percentiles for very short
  samples have little statistical value.
- Uploads are SHA-256 verified by the peer. Download verification occurs after
  timing. Python chunks/write calls are counted.
- Optional SDK checksum calculation/validation is disabled equally for the
  performance samples. Compiled integration tests separately check CRC32.

All file paths use the warm operating-system page cache. There is no `fsync`,
so completion does not measure crash durability or physical-disk throughput.
Loopback can expose runtime overhead that WAN latency hides. Report CPU and
memory separately from elapsed time, and do not turn these results into an AWS
throughput claim.

The native file buffer and both Python writer buffers are 1 MiB. Total memory
also includes SDK, transport, Python and operating-system buffers. A native
path does not imply zero-copy or a lower peak RSS in every scenario.
