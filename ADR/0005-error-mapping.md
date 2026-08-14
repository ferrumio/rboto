# ADR-0005: Map SDK failures to generated Python exceptions

- Status: Accepted
- Date: 2026-08-14
- Deciders: rboto maintainers

## Context

The AWS Rust SDK returns operation-specific `SdkError` values. Python callers need stable,
catchable exceptions with service context instead of Rust error strings or a single opaque
exception.

## Decision

Each service has a generated base exception and modeled exception subclasses. The bridge
normalizes AWS error codes, extracts the request ID and message when available, records the
operation name, and raises the most specific generated Python exception. Unknown service
errors fall back to the service base exception.

Compatibility metadata is available through the shared `ServiceError` API, but exception
classes rather than response dictionaries are the primary control-flow mechanism.

## Consequences

- Callers can catch modeled failures without parsing strings.
- Error normalization is shared in `rboto-core` and generation remains service-specific.
- LocalStack tests should verify representative modeled failures.
- New modeled errors are added through regeneration.

## Alternatives considered

- Raise one generic exception: rejected because it loses modeled behavior.
- Expose Rust errors directly: rejected because they are not a stable Python API.
- Reproduce the complete botocore exception implementation: rejected because rboto only
  preserves compatibility fields that serve a concrete use case.

## References

- `crates/rboto-core/src/lib.rs`
- `packages/rboto/src/rboto/exceptions.py`
- `packages/rboto-*/python/*/exceptions.py`
