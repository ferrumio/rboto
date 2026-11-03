# Code Generation Guide

## Change the correct layer

- Service metadata or feature toggle: edit `codegen/src/rboto_codegen/services/*.toml`.
- Parsed descriptor fields: edit `model.py` and `registry.py`.
- Type conversion or operation behavior: edit `generator.py`.
- Repeated file structure: edit a Jinja template.
- Package descriptions and guide paths: edit `display_name` and `documentation_path`
  in the service descriptor.
- Package usage examples: edit `examples/clients/<python_name>.py`. README operation
  tables and full signatures come from the in-memory Python API during generation.
- Operation references: `reference.py` reads the in-memory client, types and native
  stub contracts, with pinned model notes and resolved pagination metadata.
  `reference_site.py` generates and validates pages under ignored `docs/reference/`
  only for website builds. Do not commit or package generated reference pages.
  Methods and public types carry short docstrings with matching website URLs.
- Handwritten runtime behavior: edit the relevant non-generated Rust runtime file.
- Pagination traits: parsed in `smithy.py`, validated in `pagination.py`, and
  rendered in `paginators.py.j2`. Missing metadata belongs in the service TOML
  `[pagination.OperationName]` tables; use modeled member names and validate
  against the pinned model. Runtime iteration lives in `rboto/_pagination.py`.
- Waiter traits: parsed by `waiter_metadata.py` from Smithy or service TOML
  `[waiters.Name]` tables, validated/compiled in `waiters.py`, rendered through
  `waiters.py.j2`. Use the Smithy matcher schema; supplements also need `operation`.
  The runtime is `rboto/_waiters.py`. Reject unsupported expressions, stream outputs,
  unmodeled errors, and parameter/name collisions. See ADR-0026.
- Test fixtures: operation factories and reachable response converters are generated
  from Rust output fields/builders. Keep fixture validation outside normal conversion
  and getter paths. `rboto/testing.py` validates input hints and manages client-scoped
  expectations through middleware. Never replace native Outputs with dicts or relax
  replacement-type checks. See ADR-0028.

Do not add service-name branches to generic generation until a declarative customization
has been shown to be insufficient.

Native child ownership is generated through `_shared_storage` and `_into_shared`.
Move payloads into independent child nodes; getters clone only their references.
Decide which collections need transformation from their parsed structure, not
type-string formatting. Keep streams outside shared metadata and leave dictionary
unions on their existing conversion path. See ADR-0025.

Recursive `JSONValue` uses a Python 3.12 named type alias, so optional annotations
resolve through `typing.get_type_hints` and schema generators can follow its
recursive definition. Do not replace it with a quoted string assigned to
`TypeAlias`: `JSONValue | None` would then fail during runtime introspection.

## Required workflow

1. Update the source layer.
2. Run `.venv/bin/rboto-codegen generate --all`.
3. Review generated diffs for every service, including unexpected removals.
4. Run codegen tests and strict Python typing.
5. Run Cargo format, tests, and Clippy.
6. Rebuild native extensions before compiled bridge or LocalStack tests.

## Alignment

Smithy defines operations, members, requiredness, enums, and modeled errors. The AWS Rust
crate defines concrete module paths, builders, output structs, and ownership constraints.
Generation must respect both. Do not silence an alignment mismatch without documenting why
the SDK representation is intentionally different.

See ADR-0002 and ADR-0003.
