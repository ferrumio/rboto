# ADR-0010: Validate the newest CPython ABI and smoke-test every release target

- Status: Accepted
- Date: 2026-10-27
- Deciders: rboto maintainers
- Supersedes: [ADR-0007](0007-ci-and-release-matrix.md)

## Context

Python 3.15 support requires a compatible PyO3 runtime, correctly tagged wheels, and
compiled bridge tests. Metadata alone cannot establish ABI compatibility. The prior
release workflow built several targets but only smoke-tested Linux x86_64 on Python 3.12.

## Decision

Support standard CPython 3.12, 3.13, 3.14, and 3.15. Pin PyO3 0.30.1 and
pyo3-async-runtimes 0.30.0 together, and require maturin 1.15 or newer for native builds.
This decision does not add free-threaded Python, PyPy, Windows, or abi3 wheels.

Routine CI builds every service wheel on Linux with Python 3.15 and runs the compiled
method contracts and protocol tests. Python/codegen checks and representative LocalStack
flows remain on Python 3.12, exercising the minimum supported interpreter without
multiplying the complete per-service CI matrix.

Releases build all four CPython versions on Linux x86_64/aarch64 and macOS x86_64/arm64.
Each interpreter and platform combination installs the complete wheelhouse and
instantiates all service clients before any service distributions are published.
macOS architectures use separate native runner jobs so adding Python versions does not
multiply both architectures within one GitHub Actions matrix.

## Consequences

- Every pull request exercises the newest supported native ABI and the minimum Python API.
- Release smoke failures on any supported interpreter or platform block publication.
- Python 3.13/3.14 and macOS execution are validated in releases rather than every PR.
- Expanding platform support requires updating metadata, build matrices, smoke tests,
  documentation, and this decision through a superseding ADR.

## References

- [PyO3 0.30.1 changelog](https://github.com/PyO3/pyo3/blob/v0.30.1/CHANGELOG.md)
- `.github/workflows/ci.yml`
- `.github/workflows/integration.yml`
- `.github/workflows/release.yml`
