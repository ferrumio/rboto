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
- Linux glibc (manylinux 2.17) and musl (musllinux 1.2), x86_64 and aarch64.
- macOS x86_64 and arm64.
- Service wheels and source distributions.
- Core wheel and source distribution.

PyO3 does not make a Linux binary valid on macOS. Maturin must compile and link each target.
The release workflow must retain target-specific builds and wheelhouse smoke validation
for every supported interpreter and platform. Both macOS architectures use native runners
and separate job matrices. Publication waits for all wheelhouse smoke jobs.

Native packages require maturin 1.15+. PyO3 and pyo3-async-runtimes must be upgraded
together and validated with rebuilt extensions on the minimum and newest CPython.

See ADR-0017.

### Preparing and validating a release

All packages use the same `X.Y.Z`; previews use `0.x.x` without a prerelease suffix.
Version preparation remains separate from publication. After merging a version,
dispatch `release.yml` with that version and leave `publish` unchecked to build and
validate the complete wheelhouse without uploading to PyPI or creating a GitHub release.

The build workflow groups at most six services per platform job and builds all
four interpreters for each service sequentially. The catalog balances group size
and spreads large generated Rust adapters across jobs. With 34 services there are
six groups of five or six, giving 36 wheel jobs and six source-distribution jobs.
All platform groups, source-distribution groups, and smoke jobs run without
workflow-imposed matrix concurrency caps, subject to GitHub runner availability
and account limits. Each build keeps two Cargo jobs per runner.
Dependencies remain in the shared Cargo target directory;
the selected native adapter is cleaned before building to prevent another
service's cached `_native` library from being packaged. Clear compiler wrappers
only for that cleanup command: the action sets `RUSTC_WRAPPER` before the Linux
hook but installs sccache after it. Musl builds disable the
action's sccache installer because it requires a wheel unavailable for musl.

Each service uploads its own artifact immediately. A failed group can therefore
retain earlier successful artifacts; rerunning that job rebuilds the group.
Service durations, including setup and upload, appear in the job summary and a
`timings--<batch>--<platform>` CSV artifact. GitHub records the total job duration.

Each smoke job installs wheels only. Musl smoke jobs execute in the matching
musllinux container with networking disabled. The descriptor-derived catalog
selects client classes; do not enumerate `rboto.__all__` as if all public symbols
were constructors.

`release_artifacts.py validate` checks all expected interpreter/platform wheels,
source distributions, metadata identities, and typing files before producing
`SHA256SUMS`. Release artifacts and the catalog are retained for 14 days.

### Publishing and recovery

A `vX.Y.Z` tag or a manual dispatch with `publish=true` publishes after all validation
jobs pass. All publisher jobs use environment `pypi`. Services publish in separate
jobs without a matrix concurrency cap; the facade publishes last.
Runs for the same version remain serialized to avoid overlapping publication.

Publication uses the `PYPI_API_TOKEN` environment secret. Both publisher jobs fail
explicitly if it is missing. They do not request an OIDC token, and digital
attestations are disabled because the publishing action requires Trusted
Publishing for them. Validation-only runs do not need the secret. See ADR-0022.

If publication is interrupted, rerun the failed jobs in the original Actions run.
They download the original artifacts, compare local hashes with `SHA256SUMS`, and
query PyPI. Identical files are skipped, different bytes fail, and only missing
files are uploaded. The job verifies the complete project after upload. Do not
rebuild files under a published version to recover a partial upload: native builds
can differ byte-for-byte.

The GitHub release is finalized only after PyPI publication. Its assets can be
retried from the same run.

### One-time PyPI setup

Create a PyPI API token with `Entire account` scope on the publishing account,
then store it in the repository's `pypi` environment as `PYPI_API_TOKEN`.
One token covers all release projects that the account can publish. A
project-scoped token cannot create or publish the other service projects.

In GitHub, use Settings → Environments → pypi → Environment secrets. Do not
commit the token or put it in workflow inputs. Confirm the secret exists before
starting a publishing run; token checks run in the publisher jobs after builds
and validation.

Before the first publication, create every release project under `ferrumio`
and grant the token's account publishing access. Use the organization's
Projects page to create the empty projects. The maintainer chose organization
ownership before upload: do not start a publishing run until all projects
are created. Organization membership and token authentication alone do not
assign new uploads to the organization. Tokens do not bypass PyPI's
project-creation limits.

The release catalog still includes `trusted-publishers.csv` as a project
inventory and for a possible future switch to Trusted Publishing. Registering
these publishers is not required by the current token-based workflow.

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

The optional `package_version` dispatch input prepares an `X.Y.Z` rboto release.
It updates the workspace, core and codegen versions; generated service versions and their
core dependency pins follow the workspace version. Extras and `Cargo.lock` are refreshed
as part of generation/resolution. Versioned update branches are separate from model-only
updates so the next schedule does not erase a pending version bump.

Leaving `package_version` empty preserves the rboto package version. Neither the schedule
nor manual model updates publish packages. After merging a prepared version, create its
`vX.Y.Z` tag or dispatch `release.yml` with that same version and `publish=true` to
build, smoke-test, and publish the full wheelhouse. Without `publish=true`, manual
dispatch only validates. Release preparation checks both manifest versions and the
mutual core/service dependency pins.

See ADR-0011.
