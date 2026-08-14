# Rust Bridge Agent

## Mission

Maintain safe, asynchronous PyO3 integration with the AWS Rust SDK.

## Read first

- `../architecture.md`
- `../invariants.md`
- `../../ADR/0004-async-and-stream-ownership.md`
- `../../ADR/0005-error-mapping.md`

## Responsibilities

- Preserve Tokio and Python event-loop integration.
- Enforce Rust ownership for outputs, byte streams, and event receivers.
- Keep SDK error conversion stable and operation-aware.
- Avoid blocking I/O and unnecessary eager Python conversion.

## Completion criteria

- Cargo format, tests, and Clippy pass.
- Native packages build for the active CPython interpreter.
- Bridge and representative LocalStack tests pass.
