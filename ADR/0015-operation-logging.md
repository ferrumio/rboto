# ADR-0015: Configure client logging globally with ready-made JSON output

- Status: Accepted
- Date: 2026-10-28
- Deciders: rboto maintainers

## Context

Applications need consistent operation logs across all clients with minimal setup,
service-specific logger names, and independent level/disable controls. Requiring
applications to implement JSON formatting and register instrumentation on each
client adds setup unrelated to their operations.

## Decision

`configure_logging()` configures the rboto logging namespace once. It provides a
JSON handler on stdout, a default level, and optional service-level overrides.
`OFF` disables logging. Reconfiguration replaces levels and overrides while reusing
the owned handler. The application root logger and its handlers remain unchanged;
rboto records stop at the rboto namespace to avoid root-handler duplication.

Each service uses `rboto.<service>`. `get_logger(service)` returns that standard
logger for individual level changes and disabling. Generated clients expose the
name as `logger_name`. Configuration applies to clients created before or after it.
Generated operation methods resolve logging at invocation time. Disabled logging
bypasses its middleware and native metadata collection; existing application
middleware is preserved.

The internal logging middleware wraps application middleware automatically and
uses the lifecycle from ADR-0014. INFO records success, ERROR failure, WARNING
cancellation, and DEBUG adds bounded retry diagnostics. JSON includes timestamp,
level, logger, operation identity, correlation, duration, and native metadata.
Final outcome and native failures are separate fields.

Inputs, outputs, streams, exception messages, and tracebacks are never inspected.
An optional synchronous trace provider supplies IDs on the operation task without
a telemetry dependency. Handler/provider failures do not replace operation results
or original exceptions. Process-control exceptions retain normal Python behavior.

The JSON stream is configurable and application-owned streams are never closed by
rboto. Configuration does not add tasks or dependencies. Native sources and public
operation signatures remain unchanged.

## Consequences

- One call provides usable JSON logs across the client catalog.
- Application configuration controls the default and each service independently.
- Synchronous destinations can delay the caller; delivery is diagnostic and is not
  retried or guaranteed.
- Completion of an operation does not include later response stream consumption.
