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

## Creating an ADR

Copy `template.md`, assign the next number, and add it to this index. Existing accepted
ADRs are immutable except for status and links. A changed decision should normally be a
new ADR that supersedes the old one.
