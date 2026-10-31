# ADR-0022: Publish the package family with a PyPI API token

- Status: Accepted
- Date: 2026-10-31
- Supersedes: the publication authentication decision in [ADR-0017](0017-release-validation-and-musllinux.md)

## Context

The first public release contains 35 distributions. PyPI requires a separate
Trusted Publisher association for each project, including projects owned by an
organization. The maintainer chose one API token for publishing the package
family.

## Decision

Store a PyPI token with `Entire account` scope as `PYPI_API_TOKEN` in the GitHub
`pypi` environment. Both the service publishers and the facade publisher pass
this secret to the publishing action. Fail explicitly if the secret is absent.
Keep it out of build jobs, workflow inputs, and logs.

Publisher jobs no longer request `id-token: write`. Disable the publishing
action's digital attestations, which require Trusted Publishing. Documentation
deployment keeps its existing OIDC permission.

Keep the complete build and smoke matrix, checksum validation, service-first
publication, and recovery from original artifacts. Validation-only runs do not
require publication credentials.

Keep existing projects under the PyPI organization `ferrumio`. The maintainer
authorized first uploads to create remaining projects under the token's
account, followed by transfer to `ferrumio` in PyPI's organization interface.
If project creation reaches PyPI's quota, respect its retry interval and resume
failed publishing jobs from the original run without rebuilding artifacts.

## Consequences

- One secret covers all projects the publishing account can access, including
  future services.
- The token grants access beyond a single project and requires rotation or
  revocation by the maintainer.
- The token does not assign organization ownership. Projects created under
  the publishing account need a subsequent transfer to `ferrumio`.
- PyPI's project-creation limits still apply.
- Releases retain their checksums but do not upload digital attestations.
