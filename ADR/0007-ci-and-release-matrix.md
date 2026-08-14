# ADR-0007: Separate fast CI validation from release builds

- Status: Accepted
- Date: 2026-08-14
- Deciders: rboto maintainers

## Context

Native wheels are specific to the operating system, architecture, and CPython ABI. Building
the complete distribution matrix on every pull request is expensive, while not building
native extensions would leave the Python-to-Rust boundary untested.

## Decision

Regular CI uses Linux and CPython 3.12. It runs Python/codegen quality checks, Rust quality
checks, one release wheel build per service, and debug native builds for LocalStack bridge
tests. This validates packaging and the complete runtime path without multiplying routine
builds across platforms and Python versions.

The release workflow remains responsible for the supported binary matrix: CPython
3.12-3.14, Linux x86_64 and aarch64, and macOS x86_64 and arm64. Release artifacts are smoke
tested from the wheelhouse before publication.

## Consequences

- Pull-request CI is materially faster.
- macOS and additional CPython ABI failures are detected at release time, not on every PR.
- Release workflow health is part of platform-support confidence.
- Adding a supported platform requires a release-matrix change and may justify a targeted
  scheduled CI job.

## Alternatives considered

- Full matrix on every pull request: rejected because of cost and feedback time.
- Linux-only releases: rejected because macOS is a supported platform.
- Rely on PyO3 to infer cross-platform correctness: rejected because PyO3 and maturin still
  compile and link target-specific binaries.

## References

- `.github/workflows/ci.yml`
- `.github/workflows/integration.yml`
- `.github/workflows/release.yml`
