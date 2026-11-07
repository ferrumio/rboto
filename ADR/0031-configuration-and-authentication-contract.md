# ADR-0031: Define configuration and credential-provider compatibility

- Status: Accepted
- Date: 2026-11-07
- Extends: ADR-0012
- Supersedes: ADR-0016 only for proxy inheritance when additional CA trust is set

## Decision

Retain the SDK-owned default credential chain, provider refresh and per-client
identity caches. Enable `aws-config/credentials-login` alongside its existing SSO
and process providers. Consuming and refreshing a cached console login is supported;
interactive login remains an external CLI operation. Do not add a second credential
loader, Python provider callback or cross-wheel credential broker.

Resolve additional trust lazily from explicit `ClientConfig.ca_bundle`, then
`AWS_CA_BUNDLE`. Reject empty or non-UTF-8 environment paths with bounded
`ConfigurationError`; an explicit path takes precedence without reading the
environment value. Build the shared provider/service transport on a blocking worker,
retaining certificate/hostname verification and connector deadlines.

Separate proxy selection from trust selection. Without `proxy_url`, a custom
CA transport inherits environment proxy settings through the same SDK `ProxyConfig`
used by its default transport. With `proxy_url`, only explicit proxy/bypass rules
apply. `no_proxy` continues to require `proxy_url`. Constructor validation does not
resolve ambient proxy rules; those are resolved during native initialization.
Do not mutate process environment variables.

This changes the earlier ADR-0016 behavior where setting only CA trust silently
disabled environment proxies. All other ADR-0016 ownership and isolation rules remain.
Each client resolves its configuration once; explicit credential snapshots remain
static while refreshable providers continue to manage renewal.

## Validation and public contract

Document a user-facing environment/authentication matrix, separating compiled
regression coverage from inherited SDK settings and unsupported interactive flows.
Avoid claiming universal parity with other SDKs or all upstream environment variables.

Compiled STS tests use private HTTP peers and fabricated profile/cache/token files
to exercise precedence, web identity, container token-file selection, IMDSv2,
legacy/modern SSO, console-login refresh and sanitized provider failures.
Bedrock Runtime verifies bearer authentication without SigV4 credentials.
TLS/proxy tests exercise explicit/environment CA paths, explicit/environment proxy
precedence, bypass, hostname validation and AssumeRole using the same transport.
These checks do not replace real IAM/organization configuration validation.

Operation/stream error normalization and richer exception metadata are tracked in
issue #90. Parser/alignment/conversion hardening is tracked in issue #91.
