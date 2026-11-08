# ADR-0028: Report field paths on input conversion failures

- Status: Accepted
- Date: 2026-11-03
- Amended by: [ADR-0034](0034-reject-unknown-input-structure-fields.md) for unknown structure fields

## Decision

Enrich failures already detected while converting Python inputs, without adding
a second validation pass or changing accepted values. Build paths while an error
unwinds through generated fields and containers. Successful conversions do not
allocate a path or format diagnostic text. Collection indexes are local counters.

Keep existing exception classes and identities. Expose a frozen
`ConversionErrorContext` through `get_conversion_context(error)` in
`rboto.exceptions`: service ID, modeled operation name, public Python field path,
expected type/rule, category and a truncation flag.

Adapter-created builtin exceptions receive a bounded diagnostic message.
Exceptions originating in application Python code retain their messages,
tracebacks and causes and receive a contextual note instead. Do not interpolate
input values or call their repr/str methods to build diagnostics.

Paths contain modeled field names, list indexes and map entry positions.
`[value#0]` and `[key#0]` identify the first map entry without exposing its key.
Bound the path to 512 characters, expected metadata to 160, and service/operation
metadata to 128 each. Report truncation explicitly.

Nested builder failures identify missing fields on the failure path. Read the
builder's explicit `missing_field` checks during crate parsing: non-optional
native fields can have defaults and do not establish requiredness.
Middleware's existing top-level validation uses the same context contract.
Python argument binding errors occur before the generated method body and keep
Python's standard diagnostics. SDK serialization, transport, service errors and
deferred event-stream failures retain their existing classification.

## Consequences

Exception text changes for adapter conversion errors. Catching TypeError,
ValueError, OverflowError or application exceptions continues to work; consumers
should use structured context instead of parsing messages.

Application-supplied exception messages and causes are outside the diagnostic
redaction guarantee. rboto preserves them rather than silently removing a
caller-defined error chain. Automatic context never includes arbitrary map keys
or input contents.

All services inherit paths through core helpers and generic generation. Validate
compiled paths, privacy bounds, cause preservation, concurrent isolation and
successful-conversion overhead before shipping.
