# ADR-0032: Preserve typed error context across response consumption

- Status: Accepted
- Date: 2026-11-07
- Extends: ADR-0005, ADR-0012, ADR-0014, ADR-0029

## Decision

Use the shared `ClientError` hierarchy for operation dispatch, byte collection,
byte iteration and event receivers. Walk typed native causes to distinguish
connection, timeout and response failures. Do not classify by formatted text.
Keep opaque body/validation failures as `ResponseError`; the SDK's private
minimum download throughput error cannot currently be distinguished by type.
Preserve modeled service exceptions, Python producer exceptions and cancellation.
The explicit resource closure contract and input conversion boundary do not change.

Expose a `ResponseMetadata` typed dictionary through `ClientError.response_metadata`
and `response["ResponseMetadata"]`. Its four keys are HTTPStatusCode, RequestId,
HostId and RetryAttempts. Unknown values are None. Request IDs come only from
recognized headers or modeled request metadata, are at most 1,024 ASCII graphic
bytes, and never include arbitrary headers or payloads. RetryAttempts excludes
the initial attempt. Metadata represents the last attempt, not an earlier retry.

The existing per-operation native observation hook always retains bounded
response context and counts attempts. Detailed timing and the 32 attempt records
remain opt-in. Disabled Python middleware still uses direct dispatch without a
Python metadata sink or Python success-metadata objects. The added native
collector is an explicit cost for truthful retry metadata on errors.

When returning a stream, snapshot operation and response metadata into an
independent Arc. Advancing a stream clones only the Arc, not the IDs. Do not retain
the client, SDK response, request, body or detailed attempt history in that context.
Stream errors report the initial response status and operation retries; they do
not dispatch another attempt or restart a consumed body.

Parse public modeled error fields from pinned Rust sources. Generate converters
only for additional modeled fields, excluding message (already a shared field).
Expose these through exception-specific TypedDict schemas in service types and
typed `details` properties in service exceptions. Nested error data uses the
existing dictionary-output converters and schemas. Unknown variants and payload
fields remain unexposed. Details can contain application data; they are excluded
from standard exception formatting, compatibility response dictionaries, automatic
logging and tracing. Supplemental conversion must not replace an original error.

## Validation

Regenerate every service. Compile all adapters and check strict Python contracts.
Controlled peers exercise errors before headers, retries with and without
observation, truncated byte reads and iteration, checksum validation, malformed
event frames/payloads and modeled event errors. Verify retained context after
client closure, independent concurrent responses, original producer exceptions,
cancellation and existing resource-close behavior. Unit tests cover typed timeout
causes and bounded metadata. No live account resources are required.
