# ADR-0002: Generate bindings from Smithy and the AWS Rust SDK

- Status: Accepted
- Date: 2026-08-14
- Deciders: rboto maintainers

## Context

AWS services contain hundreds of operations and thousands of modeled shapes. Handwritten
bindings would drift from both the public service model and the concrete Rust SDK API.
Smithy describes the service contract, while the selected AWS Rust SDK crate determines
the exact builders, output structs, and Rust types that must compile.

## Decision

The generator uses pinned Smithy models for public API semantics and parses the pinned AWS
Rust SDK crates for concrete Rust alignment. Generation must fail when a required type or
conversion cannot be represented safely.

Generic behavior belongs in `generator.py` and Jinja templates. Service-specific behavior
is enabled declaratively in `codegen/src/rboto_codegen/services/*.toml`. Generated Rust and
Python files are committed for review and packaging but are never the source of truth.

## Consequences

- Generic generator changes must regenerate every service.
- Model and crate versions must remain locked and alignment-tested.
- Manual edits to generated files will be overwritten and are not accepted as fixes.
- Explicit service customization is preferred over service-name conditionals in templates.

## Alternatives considered

- Smithy-only generation: rejected because it cannot guarantee compatibility with the
  concrete Rust crate.
- Rust-source-only generation: rejected because Rust source does not carry the full public
  Smithy contract and documentation semantics.
- Handwritten adapters: rejected because coverage and maintenance do not scale.

## References

- `codegen/src/rboto_codegen/generator.py`
- `codegen/src/rboto_codegen/alignment.py`
- `codegen/src/rboto_codegen/services/`
- `codegen/models/`
