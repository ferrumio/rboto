# ADR-0017: Validate complete releases and add musllinux

- Status: Accepted
- Date: 2026-10-29
- Supersedes: [ADR-0010](0010-python315-validation-and-release-matrix.md)

## Context

The public facade exports helpers as well as clients. Calling every symbol in
`rboto.__all__` no longer smoke-tests a release. Native releases also need a musl
target and a way to validate all artifacts without publishing. Upload failures
can leave only part of a release on PyPI.

## Decision

Keep a single `X.Y.Z` version for the facade, services, and code generator.
Preview releases use `0.x.x`; no prerelease suffix is required.

Retain standard CPython 3.12–3.15 and individual interpreter ABIs. Build Linux
glibc and musl 1.2 wheels for x86_64/aarch64 and macOS wheels for x86_64/arm64.
Do not add Windows, PyPy, free-threaded Python, or abi3.

Build four interpreters per service/platform job, reusing Cargo dependencies.
Limit concurrent platforms and services. The routine CI remains focused on
Linux CPython 3.15 and representative Python 3.12 integration tests.

Freeze client names and package identities from descriptors in `release.json`.
Install every service from wheels with source fallback disabled on each of the
24 interpreter/platform combinations. Run musl smoke tests in musl containers,
with networking disabled. Import the installed extensions, instantiate only
catalog clients, check versions and typing files, and verify async/native method
alignment.

After all smoke jobs, check the complete artifact matrix, distribution metadata,
and SHA-256 hashes. Manual dispatch defaults to validation without publication.
Tags or an explicit `publish=true` enable publication after these checks.

Publish each service in a separate OIDC job using the same `pypi` environment.
Publish the facade only after all services are complete. Before retrying, compare
each artifact against its validated checksum and the checksum returned by PyPI.
Skip only identical files; differing bytes fail. Retry failed jobs from the
original run to reuse its artifacts.

PyPI requires a one-time publisher association for each project. Generate a CSV
of registrations from the catalog. Pending publishers can be configured under a
PyPI organization so new projects belong to that organization.

## Consequences

- The 34-service release contains 816 native wheels, 34 service sdists, and two
  facade artifacts.
- A successful dry run is evidence of the complete declared platform matrix;
  ordinary CI alone is not.
- Publishing remains non-atomic across PyPI projects. Publishing the facade last
  reduces exposure through extras but does not hide partial service uploads.
- Rebuilding an already uploaded version may produce different bytes. Recovery
  uses the original artifacts or a new version, never overwrites PyPI files.
- Validation does not prove every operation works against a live service.
