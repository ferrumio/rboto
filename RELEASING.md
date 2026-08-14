# Releasing rboto

## One-Time Repository Setup

Create these GitHub environments:

- `pypi-services`
- `pypi`
- `aws-smoke`
- `github-pages`

Reserve and configure Trusted Publishers for every PyPI project:

- `rboto`
- `rboto-s3`
- `rboto-sqs`
- `rboto-dynamodb`

All service projects use `.github/workflows/release.yml` with the
`pypi-services` environment. The core project uses the same workflow with the
`pypi` environment.

Configure the `aws-smoke` environment with approval protection. The real AWS
workflow receives an IAM role ARN and assumes it through GitHub OIDC. Permanent
AWS access keys are not required.

Enable GitHub Pages with GitHub Actions as the deployment source.

## Preparing a Version

```bash
python .github/scripts/set_version.py 0.2.0
.venv/bin/rboto-codegen fetch-model --all
.venv/bin/rboto-codegen generate --all
cargo fmt --all
.venv/bin/python .github/scripts/check_version.py --expected 0.2.0
```

Commit the version change and generated sources. The generated-source workflow
must pass with a clean diff before tagging.

## Publishing

```bash
git tag v0.2.0
git push origin v0.2.0
```

The release workflow performs these steps:

1. Validates synchronized versions.
2. Builds CPython 3.12, 3.13, and 3.14 wheels.
3. Builds manylinux 2.17 x86_64/aarch64 and macOS x86_64/arm64 wheels.
4. Builds service and core source distributions.
5. Installs all extras from the Linux wheelhouse.
6. Publishes service packages.
7. Publishes the core `rboto` package last.
8. Creates a GitHub Release with all artifacts.
9. Deploys API documentation.

Publishing the core last prevents an extra from referencing a service version
that is not yet available on PyPI.

## Updating AWS Models

Run the `Update AWS models` workflow manually with an AWS SDK release tag or let
its weekly schedule use the latest release. It updates locked model hashes and
crate versions, regenerates all sources, compiles the workspace, and opens a PR.

AWS SDK and Smithy dependencies are intentionally excluded from Dependabot.
