# ADR-0023: Website operation references and docstring links

- Status: Accepted
- Date: 2026-11-03

## Decision

Generate operation and public type references during documentation builds from
the pinned models and the same in-memory contracts used for bindings.
`docs/reference/` is ignored. Generated pages are neither committed nor shipped
in wheels or source distributions.

Keep complete signatures, linked nested types, Output fields, modeled errors,
pagination, streaming behavior and existing checked examples on the website.
Escape and bound model excerpts. Preserve recursion, native public aliases,
field presence and nullability. Type filenames that collide under case folding
receive deterministic suffixes.

Generated methods and public types contain short docstrings linking to their
reference pages. Native class stubs carry the same links. Functional TypedDicts
and type aliases use attribute docstrings for editors; do not mutate their
runtime objects or change their definitions to attach documentation.

URLs use stable service, operation and type paths. Each reference states the
source version; the website follows development source and may precede PyPI.
The site is not a historical release archive.

## Validation

Check signatures, imports, all local links and every generated docstring target.
Exercise recursive types, output-only unknown variants, native alias collisions,
streams and case-insensitive paths. Build the website strictly and regenerate
all services. Python APIs and Rust runtime sources remain unchanged.
