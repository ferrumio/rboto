# ADR-0024: Bound and configure per-service runtime workers

- Status: Accepted
- Date: 2026-11-03

## Decision

Keep one lazy Tokio runtime per extension. The Python facade owns a locked
integer policy shared by all extensions: explicit `configure_runtime()` setting,
then `TOKIO_WORKER_THREADS`, then `min(2, available_parallelism())`. Reject invalid
counts and booleans. Do not mutate process environment variables.

Resolve and freeze the count when the first native runtime initialization starts.
Equivalent subsequent configuration is allowed; conflicting configuration raises
`RuntimeError`. Later services use the frozen count even if their environment or
available CPU count differs. Invalid configuration does not freeze the policy.
A failure to build a runtime retains the resolved policy; retries use that count.

The shared core wraps the existing async future bridge. The first call resolves
the Python policy and builds/registers an owned runtime. PyOnceLock detaches while
waiting for concurrent initialization; pool construction also releases the GIL.
Subsequent calls take the initialized fast path without consulting Python policy.
Resolve the originating event loop before starting a pool so invalid calls outside
an event loop do not freeze policy or create workers.

All operation, byte-stream and event-stream futures use the same wrapper.
Cancellation and task-local context remain delegated to pyo3-async-runtimes.
Runtimes and their Rust references remain within their own extension libraries.
Only a Python integer crosses the wheel boundary.

The private `_runtime_worker_threads()` module function reports the actual native
worker count without initializing a runtime. Compiled tests use it to distinguish
runtime workers from transient blocking threads and verify per-extension lifetime.
It is not a public tuning or diagnostics API.

## Validation

Exercise precedence, invalid values, equivalent and conflicting configuration,
and concurrent resolution in Python. Fresh compiled subprocesses verify lazy
imports/construction, direct and facade imports, concurrent first use across
Python threads and event loops, separate wheels, reuse and runtime lifetime.
Existing stream and modeled-error tests exercise the common bridge.

Compare 1, 2, 4 and the previous CPU-count setting using release wheels, a separate
local HTTP peer and randomized fresh processes. Record throughput, latency, CPU,
thread counts, RSS and event-loop gaps. See the
[runtime benchmark report](../benchmarks/runtime_workers.md) for results and
the limits of a local peer.
