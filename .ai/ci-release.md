# CI and Release Guide

## Routine CI

`.github/workflows/ci.yml` runs Python/codegen quality, Rust quality, and one Linux CPython
3.15 wheel build per service, discovered from the descriptors. Each wheel job installs its
wheel and runs its compiled bridge and local protocol tests, including HTTPS and credential
providers for SNS. Wheel content validation rejects interpreter caches and missing native
or typing files. Python/codegen checks remain on Python 3.12.
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

## Model updates and package versions

`update-aws-models.yml` runs weekly or manually. It updates all service descriptors,
renders package manifests with `rboto-codegen generate-manifests --all`, resolves the
workspace lock, and fetches the pinned crates before model alignment and generation.
Manifest generation does not need downloaded models or Rust sources.

The updater publishes its branch after Python checks, locked Rust compilation, version/pin
checks, and a second generation pass. It uses `GITHUB_TOKEN` and creates a PR when policy
allows it. If the organization/repository forbids bot-created PRs, the workflow keeps the
branch, provides a link to open a PR manually, and still dispatches CI, generated-source
checks, and integration on that branch. Only the explicit bot-PR policy error uses this
fallback; other publication errors fail the workflow. Existing PRs are reused.

Dispatch verifies the generated commit before starting each workflow. This is required
because the built-in token does not trigger normal PR/push events. `actions: write` is
scoped to the updater job. No App token or personal access token is required. The workflow
does not approve, merge, or publish packages, and does not change organization policy.

The optional `package_version` dispatch input prepares a stable `X.Y.Z` rboto release.
It updates the workspace, core and codegen versions; generated service versions and their
core dependency pins follow the workspace version. Extras and `Cargo.lock` are refreshed
as part of generation/resolution. Versioned update branches are separate from model-only
updates so the next schedule does not erase a pending version bump.

Leaving `package_version` empty preserves the rboto package version. Neither the schedule
nor manual model updates publish packages. After merging a prepared version, create its
`vX.Y.Z` tag or dispatch `release.yml` with that same version to build, smoke-test, and
publish the full wheelhouse. Release preparation checks both manifest versions and the
mutual core/service dependency pins.

See ADR-0011.
