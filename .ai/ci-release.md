# CI and Release Guide

## Routine CI

`.github/workflows/ci.yml` runs Python/codegen quality, Rust quality, and one Linux CPython
3.12 wheel build per service. `.github/workflows/integration.yml` builds editable debug
extensions on Linux CPython 3.12 and runs the compiled bridge, LocalStack flows, and examples.

Keep routine CI focused on fast regression feedback. Do not expand it to a full operating
system and CPython matrix without measured evidence that the added signal justifies the
cost.

## Release

`.github/workflows/release.yml` owns the complete supported binary matrix:

- CPython 3.12, 3.13, and 3.14.
- Linux x86_64 and aarch64.
- macOS x86_64 and arm64.
- Service wheels and source distributions.
- Core wheel and source distribution.

PyO3 does not make a Linux binary valid on macOS. Maturin must compile and link each target.
The release workflow must retain target-specific builds and wheelhouse smoke validation.

See ADR-0007.
