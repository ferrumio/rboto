# CI and Release Guide

## Routine CI

`.github/workflows/ci.yml` runs Python/codegen quality, Rust quality, and one Linux CPython
3.15 wheel build per service, discovered from the descriptors. Each wheel job installs its
wheel and runs its compiled bridge and local protocol tests. Python/codegen checks remain
on Python 3.12.
`.github/workflows/integration.yml` builds editable debug extensions for S3, SNS, SQS,
and DynamoDB on Linux CPython 3.12 and runs their LocalStack flows and examples.

Keep routine CI focused on fast regression feedback. Do not expand it to a full operating
system and CPython matrix without measured evidence that the added signal justifies the
cost.

## Release

`.github/workflows/release.yml` owns the complete supported binary matrix:

- CPython 3.12, 3.13, 3.14, and 3.15.
- Linux x86_64 and aarch64.
- macOS x86_64 and arm64.
- Service wheels and source distributions.
- Core wheel and source distribution.

PyO3 does not make a Linux binary valid on macOS. Maturin must compile and link each target.
The release workflow must retain target-specific builds and wheelhouse smoke validation
for every supported interpreter and platform. Both macOS architectures use native runners
and separate job matrices. Publication waits for all wheelhouse smoke jobs.

Native packages require maturin 1.15+. PyO3 and pyo3-async-runtimes must be upgraded
together and validated with rebuilt extensions on the minimum and newest CPython.

See ADR-0010.
