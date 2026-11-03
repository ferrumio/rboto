# ADR-0027: Optional operation tracing and metrics

- Status: Accepted
- Date: 2026-11-03

## Decision

Expose `configure_opentelemetry` and `OpenTelemetry` under the optional
`rboto.integrations.opentelemetry` module. Its extra installs only the
OpenTelemetry API. Default imports, operation dispatch, logging, and service
wheels require no telemetry package. Applications own providers and exporters.

Resolve one observer per invocation: explicit client/Session configuration
overrides global configuration, including explicit disabling. Place it outside
logging and application middleware. Reject multiple explicit observers.
Configuration replaces the global reference after successful construction;
in-flight operations retain the captured observer. Never patch methods or
install global providers, native tracing subscribers, or background workers.

Create one logical client span and record independent operation metrics around
the shared middleware chain. Preserve native retries, typed responses, modeled
errors, and cancellation. Final outcomes include policy failures, synthetic
results, invalid replacement responses, and recovered native errors. Keep native
diagnostics separate from logical outcomes. End on operation return/raise;
later stream consumption is excluded and never inspected.

Use OpenTelemetry's task context and restore it in `finally`. A dependency-free
logging ContextVar carries IDs while the observer wraps logging. Explicit logging
trace providers retain precedence. Custom instrumentation/exporter exceptions
must not replace results or failures; process-control exceptions remain Python
exceptions. Never record exception messages or stack traces.

Pin the Development AWS SDK convention vocabulary to semantic conventions
1.39.0. Generate the service's `sdkId` into operation context rather than
maintaining service-name branches. Use the version's generic SDK spans for all
services, without payload-derived database/messaging attributes.

Metrics have only service, operation, and bounded outcome dimensions. Request IDs
belong to spans only. Native attempt diagnostics are optional and retain the
collector's 32-event bound. Unknown counts remain unknown. Metrics recording
uses the active operation context for application-configured exemplars.

## Validation

Use in-memory providers for concurrency, parent context, cancellation, sampling,
signal independence, configuration precedence and failure isolation. Compile
representative clients and verify retry counts, request signing, JSON correlation,
modeled errors, and streaming without eager reads. Benchmark disabled and enabled
modes separately; keep network/exporter cost outside local claims.
