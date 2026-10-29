# ADR-0016: Isolate transport and credential caches per client

- Status: Accepted
- Date: 2026-10-29
- Extends: ADR-0012

## Decision

`ClientConfig` accepts `proxy_url`, `no_proxy`, and `ca_bundle`. The proxy applies
to HTTP and HTTPS destinations. Bypass rules belong to that configuration.
The PEM bundle adds trust anchors to the system roots; certificate verification
remains enabled. Proxy URLs may contain credentials and are omitted from repr.
Validation errors never include the URL or bundle contents.

Build one HTTP client when the native service initializes. Apply it to the
configuration loader before credential providers are constructed so that
AssumeRole and service requests use the same transport. Bundle I/O and parsing
run on a blocking worker, not a Python or Tokio event-loop thread. Validate PEM
and DER before the connector reaches its infallible certificate-loading API.
Connect/read deadlines and the SDK timer remain attached to custom connectors.
No environment variables are written. Explicit transports do not inherit
environment proxy rules.

## Session and credentials

Keep the current boundary: Session shares immutable settings, while each native
client owns its resolved configuration, identity cache, and connection pools.
Reusing a client reuses its provider cache. Reusing a Session across clients does
not coalesce credential refreshes. This is deliberate, not a cross-wheel cache.

A future shared cache must use an explicit provider protocol owned by the Python
facade. A broker scoped to a Session would return credential snapshots containing
access key, secret, token, and expiration over Python values. Native providers
would call the broker only when their local identity cache needs refresh, never
for every signing operation. The broker would serialize refresh per identity and
allow unrelated identities to refresh independently. Its key must include source
profile/credentials, role, external ID, region, and transport context. Secrets must
not be formatted in keys, logs, exceptions, or diagnostics.

Do not pass Rust objects, pointers, locks, futures, or SdkConfig between extension
libraries. Before enabling a broker, test simultaneous refresh from different
wheels and event loops, cancellation of the first waiter, expiration, transient
provider failure, fork behavior, and broker lifetime. That protocol and its public
API require a separate implementation; this change does not advertise shared
credential caching.

## Diagnostics

Operation middleware and global logging remain the supported opt-in diagnostics
boundary. Configuration/provider failures pass through that boundary as typed
errors. Transport does not install process-global tracing subscribers or log
credentials. OpenTelemetry remains the separate work tracked by issue #37.

## Validation

Compiled clients exercise direct requests, authenticated HTTP proxies, CONNECT,
TLS proxies, bypass rules, custom trust, and concurrent client isolation against
local peers. Credential tests cover signing, profile selection, role composition,
refresh, retries, deadlines, and request IDs.
