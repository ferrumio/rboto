# ADR-0009: Cover the AWS SDK for Python service catalog with one generator

- Status: Accepted
- Date: 2026-10-25
- Deciders: rboto maintainers

## Context

The AWS SDK for Python repository contains 25 service clients at revision
`ba64dd7377013450e12c211149952910f40000f2`. rboto must implement these services while
retaining S3 and the native, typed, asynchronous API described in ADR-0001 through ADR-0005.

## Decision

Record that upstream catalog in `codegen/aws-sdk-python-services.json`. Register all its
services through TOML descriptors, pin their models and crates to AWS Rust SDK release
`release-2026-10-22`, and require Rust 1.94.1 as specified by those crates.

Traverse Smithy resource operations recursively. Align every operation with the pinned
Rust crate, including field naming, unit unions, optional streams, recursive boxed
structures, sparse collections, and concrete Rust union payload types.

Represent Smithy documents as recursive `JSONValue` aliases and convert their values in
the shared Rust core. Regular outputs retain native ownership and lazy getters.
Input event streams pull from a Python async iterator using the originating event loop.
They do not spawn an unbounded producer. Receiver errors use modeled service exceptions.

Generate facade extras and type checker paths from the descriptors. Discover wheel jobs
and compiled bridge contracts from the same registry. Every service receives a compiled
method/property contract in its CI wheel job. LocalStack remains representative.
Local HTTP peers additionally test conversions and duplex/event-error behavior.

## Consequences

- The catalog contains 26 services and 1,309 operations at the pinned release.
- The fixed four-service method counts in ADR-0006 are historical. Coverage now follows
  every pinned crate and cannot silently omit resource operations or a service package.
- `lambda_` is the Python factory for the `lambda` service; hyphenated service identifiers
  use underscores in factory and import names.
- Release builds retain the platform matrix in ADR-0007 and include every descriptor.
- Tests against local peers establish bridge/protocol behavior, not AWS service acceptance.
