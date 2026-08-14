# ADR-0004: Preserve asynchronous execution and stream ownership

- Status: Accepted
- Date: 2026-08-14
- Deciders: rboto maintainers

## Context

AWS SDK operations and response bodies are asynchronous. Blocking adapters or eager body
aggregation would defeat Rust SDK concurrency and make large object downloads expensive.
Rust streams are owned values and cannot be exposed through ordinary borrowed getters.

## Decision

Generated Python methods remain async end to end. PyO3 futures bridge Python's event loop to
Tokio and the AWS Rust SDK without a synchronous network path.

`ByteStream` values are moved exactly once from their SDK output into a Python-owned stream
wrapper. The parent output retains its non-stream metadata. Multiple responses can own and
advance independent streams concurrently. Event stream receivers use dedicated wrappers
and are excluded from the regular native-output path when required by ownership semantics.

## Consequences

- Streaming does not buffer an entire response unless the user explicitly calls `read()`.
- A stream is consumable state and must not be cloned to fake repeatable reads.
- Generated output construction requires special handling for owned stream fields.
- Functional tests must exercise concurrent streams and event-specific paths separately.

## Alternatives considered

- Always aggregate to `bytes`: rejected because it is unsafe for large objects.
- Clone streams: rejected because the underlying receiver is not cloneable state.
- Return raw Rust objects without Python iteration: rejected because it provides poor Python
  ergonomics.

## References

- `crates/rboto-s3/src/lib.rs`
- `examples/s3.py`
- `tests/integration/test_s3_client_methods.py`
