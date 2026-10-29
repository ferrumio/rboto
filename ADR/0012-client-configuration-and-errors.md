# ADR-0012: Centralize client settings and Python error mapping

- Status: Accepted
- Date: 2026-10-27
- Supersedes: the extraction sequence proposed in ADR-0008
- Extended by: [ADR-0016](0016-client-transport-and-credential-caches.md)

## Decision

Implement client configuration and error mapping in the existing `rboto-core`
crate. Service templates call these helpers and retain service construction,
operation dispatch, and stream adapters. No additional repository or publication
is required for these corrections.

`ClientConfig` is a frozen, keyword-only Python value. It contains explicit
credentials, AssumeRole settings, connection/read/attempt/operation timeouts, and
retry settings. Factories, classes, and Session accept it without changing
operation signatures. Python validates configuration before constructing a native
client; the native adapter validates again before converting durations.

Credentials are loaded lazily. Explicit credentials override the source profile's
credentials, while the selected profile still supplies other SDK settings.
AssumeRole wraps the resolved source configuration, including its region and
request limits. Explicit credentials plus a role therefore assume that role.
The SDK owns provider refresh and identity caching.

Only explicitly supplied timeout/retry fields override SDK configuration. Maximum
attempts includes the initial request. Operation timeouts cover the SDK operation,
not later application reads from an output stream or SDK configuration loading.
Applications can wrap the entire await/iteration in an asyncio deadline.

Modeled service errors preserve their hierarchy, message, original code, operation,
and available request ID. Other failures use CredentialsError, ConfigurationError,
ConnectionError, TimeoutError, or ResponseError. Credential classification follows
typed causes rather than matching Debug strings. Transport errors use bounded
messages without request bodies, signing headers, or secrets.

## Boundaries

Session shares immutable settings. It does not share native SdkConfig instances
between separate extension libraries. Each service retains its lazy cached client.
Rust values are not passed across extension-library boundaries.

Per-client proxy/TLS controls, tracing/metrics, and cross-service credential-cache
sharing remain separate design work. No process-global environment variables are
mutated during client construction.

ADR-0008 remains historical context for a possible future reusable crate; its
external repository and publication sequence are not prerequisites for these fixes.

## Validation

Regenerate every service. Check strict Python types and exhaustive method alignment.
Compiled STS tests use an isolated local HTTP server and fabricated credentials to
verify source profiles, AssumeRole composition, caching/refresh, signing, retries,
timeouts, request IDs, and categorized errors. These tests do not require account
resources or exercise interactive SSO login.
