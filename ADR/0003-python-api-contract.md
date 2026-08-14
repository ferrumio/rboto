# ADR-0003: Use typed Python inputs and native immutable outputs

- Status: Accepted
- Date: 2026-08-14
- Deciders: rboto maintainers

## Context

The Python API needs strict static typing without adding runtime model construction before
every AWS request. Outputs should retain the Rust SDK value and avoid eagerly converting
large nested responses into Python dictionaries.

## Decision

Generated client methods expose keyword-only parameters. Structured inputs use
`TypedDict`, `Literal`, type aliases, and explicit tagged unions. The Python wrapper builds
a plain parameter dictionary and delegates to a method with the same name on the PyO3
client.

Regular operation outputs are frozen PyO3 classes that own the Rust SDK output. Fields are
exposed through typed, read-only properties and converted lazily. Nested output structures
are native typed objects. Every regular output also exposes `to_dict()` for interoperability.

Streaming event outputs may retain a specialized representation when ownership prevents a
safe regular output wrapper. DynamoDB `AttributeValue` remains an explicit tagged union.

## Consequences

- Python users get strict input checking and attribute-based output access.
- Output conversion cost is paid only for fields that are accessed.
- Generated stubs and runtime classes must remain aligned.
- Input and output types with the same Smithy shape may require distinct Python names.

## Alternatives considered

- Dictionaries for all outputs: rejected because access is untyped and conversion is eager.
- Dataclasses or Pydantic for all values: rejected because they duplicate Rust-owned data
  and add construction overhead.
- Native classes for inputs: rejected because plain typed mappings provide better Python
  ergonomics for request construction.

## References

- `codegen/src/rboto_codegen/templates/client.py.j2`
- `codegen/src/rboto_codegen/templates/native.pyi.j2`
- `codegen/src/rboto_codegen/templates/generated.rs.j2`
