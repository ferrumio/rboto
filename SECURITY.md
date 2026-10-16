# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.x.x   | Yes       |

Before 1.0, only the latest release of `rboto` and its service packages (`rboto-s3`,
`rboto-sns`, `rboto-sqs`, `rboto-dynamodb`) receives fixes. All packages are released
together with the same version number.

## Reporting a vulnerability

Please do not open a public issue for security problems.

Use [GitHub private vulnerability reporting](https://github.com/ferrumio/rboto/security/advisories/new)
for this repository. Include:

- A description of the issue and the affected package and version.
- Steps to reproduce, if possible.
- The potential impact.

We will acknowledge the report as soon as possible and work with you on a fix and a
coordinated disclosure.

## Scope

rboto is a thin generated bridge. Signing, credential resolution, TLS, retries, and
endpoint resolution are performed by the official AWS SDK for Rust. Vulnerabilities in
those components should be reported to the
[aws-sdk-rust](https://github.com/awslabs/aws-sdk-rust/security) project. Issues in the
generated Python API, the PyO3 adapters, the code generator, or the release pipeline
belong here.

## Security practices

- Rust and Python dependencies are locked. Dependabot keeps GitHub Actions, Python, and
  Rust dependencies updated; AWS SDK crates are updated by a dedicated, reviewed workflow.
- GitHub Actions are pinned to full commit SHAs.
- Release wheels are published to PyPI through Trusted Publishers (OIDC); no long-lived
  PyPI tokens exist.
- The real AWS smoke test assumes an IAM role through GitHub OIDC; no permanent AWS access
  keys are stored.
- Credentials, private endpoints, and build artifacts are never committed.
- The OpenSSF Scorecard runs on every push to `main`.
