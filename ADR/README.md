# Architecture Decision Records

rboto is an independent project maintained by [Ferrumio](https://github.com/ferrumio).
It is not affiliated with, sponsored by, or endorsed by Amazon Web Services (AWS).

This directory contains internal architecture decisions for rboto. These records are
separate from the public documentation under `docs/`.

## Status values

- **Proposed**: under discussion and not yet binding.
- **Accepted**: current project direction.
- **Superseded**: replaced by a newer ADR.
- **Rejected**: considered but not adopted.

## Index

- [ADR-0001: Distribute one native wheel per AWS service](0001-service-per-native-wheel.md)
- [ADR-0002: Generate bindings from Smithy and the AWS Rust SDK](0002-smithy-and-rust-sdk-codegen.md)
- [ADR-0003: Use typed Python inputs and native immutable outputs](0003-python-api-contract.md)
- [ADR-0004: Preserve asynchronous execution and stream ownership](0004-async-and-stream-ownership.md)
- [ADR-0005: Map SDK failures to generated Python exceptions](0005-error-mapping.md)
- [ADR-0006: Test generated contracts and representative AWS flows](0006-testing-strategy.md)
- [ADR-0007: Separate fast CI validation from release builds](0007-ci-and-release-matrix.md)
- [ADR-0008: Share AWS configuration, credentials, and error mapping through an external Rust crate](0008-shared-aws-core-crate.md)
- [ADR-0009: Cover the AWS SDK for Python service catalog](0009-expanded-service-catalog.md)
- [ADR-0010: Validate the newest CPython ABI and smoke-test every release target](0010-python315-validation-and-release-matrix.md)
- [ADR-0012: Centralize client settings and Python error mapping](0012-client-configuration-and-errors.md)
- [ADR-0013: Export explicit client classes from the facade](0013-public-client-classes.md)
- [ADR-0014: Expose asynchronous operation middleware](0014-operation-middleware.md)
- [ADR-0015: Configure client logging globally with ready-made JSON output](0015-operation-logging.md)

- [ADR-0016: Isolate transport and credential caches per client](0016-client-transport-and-credential-caches.md)
- [ADR-0017: Validate complete releases and add musllinux](0017-release-validation-and-musllinux.md)
- [ADR-0018: Generate distinct input and response type contracts](0018-input-and-output-type-contracts.md)
- [ADR-0019: Generate typed paginators from validated metadata](0019-typed-pagination.md)
- [ADR-0020: Expose service input and response types together](0020-service-type-namespace.md)
- [ADR-0021: Name every operation result with an Output suffix](0021-operation-output-names.md)
- [ADR-0022: Publish the package family with a PyPI API token](0022-pypi-token-publishing.md)
- [ADR-0023: Website operation references and docstring links](0023-generated-operation-references.md)
- [ADR-0024: Bound and configure per-service runtime workers](0024-runtime-worker-policy.md)
- [ADR-0025: Share native output nodes](0025-shared-native-output-nodes.md)
- [ADR-0026: Generate typed async waiters](0026-typed-async-waiters.md)
- [ADR-0027: Optional operation tracing and metrics](0027-optional-opentelemetry.md)
- [ADR-0028: Report field paths on input conversion failures](0028-input-conversion-context.md)
- [ADR-0029: Explicit async client and stream lifetimes](0029-async-resource-lifecycle.md)
- [ADR-0030: Stream request bodies from Python async iterables](0030-streamed-request-bodies.md)
- [ADR-0031: Define configuration and credential-provider compatibility](0031-configuration-and-authentication-contract.md)
- [ADR-0032: Preserve typed error context across response consumption](0032-operation-and-stream-error-context.md)

## Creating an ADR

Copy `template.md`, assign the next number, and add it to this index. Existing accepted
ADRs are immutable except for status and links. A changed decision should normally be a
new ADR that supersedes the old one.
