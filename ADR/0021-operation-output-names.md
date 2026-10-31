# ADR-0021: Name every operation result with an Output suffix

- Status: Accepted
- Date: 2026-10-30
- Supersedes: [ADR-0020](0020-service-type-namespace.md)
- Amends: the dictionary output naming policy in [ADR-0018](0018-input-and-output-type-contracts.md)

## Context

Modeled operation results mix `Response`, `Result`, `Output`, and resource
names. Moving these types into one module does not make their names predictable.
The project is in preview, and the maintainer explicitly chose a breaking
normalization without compatibility aliases.

## Decision

Every operation with a modeled return value exposes `<Operation>Output`.
Use the modeled operation name, including its acronym spelling, followed by
`Output`. Operations without a modeled result still return `None`.

Apply the name to Python methods, paginators, native stubs, and native class
registrations. Keep the existing response representation, immutability,
nullability, conversion behavior, and stream ownership.

Keep clients in `rboto` and service types in `rboto_<service>.types`. Native
exports are direct `TypeAlias` bindings to runtime classes, and native operation
outputs are also exported from the service package root. Remove previous
operation-result names instead of providing compatibility aliases.

Operation names have priority over nested structures and dictionary types.
Allocate distinct native nested class names when they collide. Distinguish
Rust operation-wrapper identifiers from nested-wrapper identifiers.
For a streaming union whose name collides with its operation result, insert
`Event` before `Output`; `ConverseStreamOutput` is the outer response, and
`ConverseStreamEventOutput` is a streamed event.

Shared modeled results still receive a separate public name per operation.
Dictionary variants shared with inputs remain distinct. Preserve input names
unless they conflict with an operation output; such inputs receive an `Input`
suffix. Resolve further conflicts by inserting `Value` before the role suffix.
Do not introduce a `Response` fallback. Allocate names deterministically from
the complete service model.

## Consequences

- Users can derive the response type name from the operation name.
- Existing imports of renamed results break intentionally.
- Native extensions must be rebuilt together with their Python files and stubs.
- Tests must cover every operation's name, absent legacy exports, streaming,
  shared result shapes, and collisions with nested native classes and inputs.
- Changes to the generic naming policy require regenerating all services.
