# Input conversion diagnostics

`input_conversion.py` measures preparation of valid DynamoDB inputs without
starting a runtime or contacting a service. It calls the native method outside
an event loop: conversion completes, then the common future bridge raises
`RuntimeError("no running event loop")`. Both builds include this fixed terminal
error, Python/native entry and disposal of the prepared Rust values.

This isolates conversion bookkeeping from network and service latency. It does
not measure completed requests, exception formatting, event-loop responsiveness,
peak memory or end-to-end release throughput.

## Method

The baseline is `b9a2aa3540cc770a0d5f93ba073fd81d2e7f72cf`. Both builds use
CPython 3.12.15 and Rust 1.99.0 on Linux ARM64. The adapter and shared core are
optimized at level 3; dependency crates reuse the development cache. This avoids
rebuilding the entire SDK in release mode, but is not the release profile with
LTO.

Build each checkout from `packages/rboto-dynamodb` with identical settings:

```bash
CARGO_BUILD_JOBS=2 \
CARGO_PROFILE_DEV_DEBUG=0 \
CARGO_PROFILE_DEV_INCREMENTAL=false \
RUSTUP_TOOLCHAIN=1.99.0 \
../../.venv/bin/maturin develop --locked \
  --config 'profile.dev.package.rboto-dynamodb-native.opt-level=3' \
  --config 'profile.dev.package.rboto-core.opt-level=3'
```

Copy each installed `rboto_dynamodb` package, including its native extension,
to a separate directory. Run the same script against each:

```bash
PYTHONPATH=/path/to/before \
  .venv/bin/python benchmarks/input_conversion.py \
  --label before --output /tmp/conversion-before.json
PYTHONPATH=/path/to/after \
  .venv/bin/python benchmarks/input_conversion.py \
  --label after --output /tmp/conversion-after.json
```

Each process warms every case 20 times, then runs 15 shuffled batches of 300
calls per case. It checks that the native runtime remains uninitialized. The
JSON records per-batch wall/thread CPU timings, interpreter/platform and the
native library SHA-256.

## Measurements, 2026-11-03

Five fresh processes per build, alternating which build runs first, pinned to
CPU 0. Compilation and other test runs were paused for the measurements. Values
are medians of the five process medians, in microseconds per call.

| Valid input | Before wall / CPU (µs) | With paths wall / CPU (µs) |
|---|---:|---:|
| Two scalar attributes | 2.679 / 2.680 | 2.605 / 2.606 |
| 200 string attributes | 75.572 / 75.577 | 75.004 / 75.008 |
| 200 nested records with maps, lists and string sets | 677.754 / 677.703 | 665.487 / 665.380 |

There was no observed conversion regression in these cases. The small decreases
are not a general speedup claim: compiler layout, allocator state and local
variation can affect this microbenchmark.

No path strings or diagnostic objects are created on successful conversion.
Generated collections carry local indexes; context is assembled as an error
unwinds. This changes error reporting, not where Python input conversion runs
or how long it holds the GIL.

Native library SHA-256:

```text
before f5a5cfec57380d420a6e1680bc492e0ce2a85d138ed749e9c59860c4d778e9ce
after  4b1fd6ed3a56e4566a7a382ca98fbce6973f025725a7eab0ad18329800b780ce
```
