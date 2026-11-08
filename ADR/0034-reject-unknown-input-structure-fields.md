# ADR-0034: Reject unknown fields in input structures

- Status: Accepted
- Date: 2026-11-08
- Deciders: rboto maintainers
- Amends: ADR-0028's preservation of accepted input values

## Context

Generated structure converters read known Python field names and previously
ignored other dictionary keys. A misspelled or wire-format field such as
`ProjectionExpression` in DynamoDB `KeysAndAttributes` was silently omitted.
The request could succeed without the projection the caller intended.

TypedDict checking detects these mistakes in statically checked code, but callers
can construct inputs dynamically and middleware can modify nested dictionaries.
Top-level public parameters already reject unknown names.

## Decision

Every generated input structure converter checks dictionary keys against its
exact Python field names before converting member values or building the SDK
structure. There are no casing aliases, automatic corrections, or opt-out mode.
Unknown keys raise `TypeError`, including when their values are `None`.
Non-string keys also raise `TypeError`.

The generator emits sorted static field-name slices. A shared native helper
checks borrowed string keys using binary search without a temporary set, copied
dictionary, or diagnostic-path allocation on success. Validation runs as each
structure is converted, not through a separate recursive walk of the request.
Empty structures accept only empty dictionaries.

Use the existing conversion context. Unknown keys have category `unknown_field`
and expected rule `known structure field`; non-string keys have category `type`.
Identify the offending key by its zero-based dictionary position, for example
`request_items[value#0][key#1]`. Unknown names, like arbitrary map keys, are
application data and must not enter diagnostics. Never call their `repr` or
`str`, including for malformed Unicode or custom key objects.

Apply this generically to structures nested in maps, sequences, unions, and
event inputs across all services. Maps and JSON documents keep arbitrary keys.
Unions keep their existing exactly-one-known-variant rule. Known optional values,
required-member handling, scalar conversion, and enum acceptance stay unchanged.
This does not add service constraint validation.

Eager input failures occur before native request dispatch. Deferred event input
structures are checked when consumed; a streaming request may already have
started. Do not buffer or pre-consume streams, or change their error handling,
to promise earlier validation.

## Consequences

- Dynamically constructed requests cannot silently lose unknown structure fields.
- Callers relying on ignored extra fields must remove or correct those fields.
  TypedDict structural subtyping may allow extra fields statically; such values
  must also be narrowed to the operation's runtime input schema.
- Each structure conversion adds a key scan and binary searches. Large payload
  values are not copied or inspected by this check.
- Paths retain ADR-0028's privacy bounds, but users locate unknown names by their
  dictionary position rather than seeing the name echoed in an exception.
- Generic generator changes require regeneration of every service and compiled
  tests for rejection before dispatch, nested paths, and valid map/document data.

## Alternatives considered

- Static checking alone: does not cover dynamic inputs or middleware.
- Silently correcting casing: hides mistakes and creates ambiguous aliases.
- Logging ignored keys: still sends a request with different semantics.
- A separate Python validation pass: duplicates native traversal and schema data.
- Rejecting arbitrary map keys: breaks attributes, metadata, and JSON documents.

## References

- Issue #95
- ADR-0003, ADR-0014, ADR-0018, ADR-0028
- `codegen/src/rboto_codegen/generator.py`
- `crates/rboto-core/src/input_error.rs`
