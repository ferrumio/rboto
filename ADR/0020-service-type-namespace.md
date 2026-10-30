# ADR-0020: Expose service input and response types together

- Status: Superseded by [ADR-0021](0021-operation-output-names.md)
- Date: 2026-10-30

## Context

Input mappings and literals were imported from a service's `types` module,
operation response classes from its package root, and nested native classes
from `_native`. Users had to know the representation of a type to import it.
Some nested native classes share names with existing typed dictionaries.

## Decision

Keep clients in the `rboto` facade and expose all service input and response
types through `rboto_<service>.types`. Include native operation responses,
nested native structures, byte streams, and event-stream receivers.

Bind native types directly with `TypeAlias` assignments. These aliases reference
the existing classes at runtime and preserve strict static typing. Do not use
Python 3.12 `type` statements for native class exports: those would create alias
objects rather than expose the runtime classes.

Preserve every existing dictionary, union variant, and literal alias. Reuse a
native class's name when it is available. On collision, append `Output`, or
`Response` when the name already ends in `Output`. Append `Value` until the alias
is distinct from every existing type and every native class's canonical name.
Allocate operation outputs before nested classes. Keep the algorithm generic
and deterministic for new service models.

Keep existing package-root response exports, native class names, and native
stubs compatible. No native conversion or additional response allocation is
introduced.

## Consequences

- Consumers import types without knowing whether their implementation is Python
  or Rust.
- `Bucket` can remain a typed dictionary while `BucketOutput` is the native
  structure returned inside S3 responses.
- Static checks must cover both import paths and reject assigning a response
  to a request type or writing a read-only native property.
- Compiled contract checks verify class identity and the availability of every
  native response type through the public type module.
- Generic generation changes require regenerating every service.
