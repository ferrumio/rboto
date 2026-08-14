# ADR-0001: Distribute one native wheel per AWS service

- Status: Accepted
- Date: 2026-08-14
- Deciders: rboto maintainers

## Context

The AWS Rust SDK is split into service crates, and each service brings a significant
dependency and binary-size cost. A single extension containing every service would make
installation, compilation, and release artifacts unnecessarily large.

## Decision

Each AWS service is distributed as an independent native wheel, such as `rboto-s3`,
`rboto-sqs`, and `rboto-dynamodb`. The pure-Python `rboto` package provides the shared
session API, exceptions, optional dependencies, and convenient service factories.

The Cargo workspace may share a small `rboto-core` crate, but service adapters must not
depend on one another.

## Consequences

- Users install only the services they need.
- Each service can evolve and compile independently.
- Releases must produce platform- and CPython-specific wheels for every service.
- Cross-service behavior belongs in the facade or core crate, not in service adapters.

## Alternatives considered

- One monolithic extension: rejected because of wheel size and build time.
- Pure Python clients: rejected because the project exists to expose the Rust SDK.
- One unrelated repository per service: rejected because codegen and release logic are
  intentionally shared.

## References

- `packages/rboto/`
- `packages/rboto-s3/`
- `packages/rboto-sqs/`
- `packages/rboto-dynamodb/`
- `crates/rboto-core/`
