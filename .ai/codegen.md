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
- Handwritten runtime behavior: edit the relevant non-generated Rust runtime file.
- Pagination traits: parsed in `smithy.py`, validated in `pagination.py`, and
  rendered in `paginators.py.j2`. Missing metadata belongs in the service TOML
  `[pagination.OperationName]` tables; use modeled member names and validate
  against the pinned model. Runtime iteration lives in `rboto/_pagination.py`.

Do not add service-name branches to generic generation until a declarative customization
has been shown to be insufficient.

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
