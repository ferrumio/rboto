# Code Generation Guide

## Change the correct layer

- Service metadata or feature toggle: edit `codegen/src/rboto_codegen/services/*.toml`.
- Generated SDK dependencies disable default features and explicitly enable
  `default-https-client` and `rt-tokio`. Descriptor `rust_features` preserves
  additional SDK defaults such as `sigv4a` or `http-1x`. Do not enable `rustls`
  or `default`: the SDK's `rustls` compatibility feature selects legacy TLS.
  Tests compare the selected features against the pinned SDK's defaults and
  reject vulnerable TLS versions in the lockfile.
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

Input diagnostics unwind through `_from_py` and `rboto-core/src/input_error.rs`.
Construct paths and format context only on failure. Use public parameter names
at the operation boundary, literal TypedDict field names for nested members,
and ordinal positions for arbitrary map keys/values and unknown structure keys.
Reject unknown structure keys through `validate_input_fields` with sorted schema
names before converting members. Keep maps and documents open; do not add service
constraint validation or echo SDK builder messages or input values. See ADR-0028
and ADR-0034.

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

Both `report` and generation enforce semantic alignment before writing service
artifacts. `rust_source.py` parses the supported declaration subset; add positive
and rejecting fixtures for new upstream syntax. Never turn an unrecognized
declaration or missing file into an empty shape.

Descriptor `alignment_extras` lists additional SDK output fields by exact
operation, member, type and optionality. `alignment_types` declares a concrete
representation of a modeled scalar shape. Both require a reason; stale or unused
entries fail validation. Do not use these for unexplained model/crate drift.

See ADR-0002, ADR-0003 and ADR-0033.
