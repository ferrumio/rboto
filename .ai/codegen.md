# Code Generation Guide

## Change the correct layer

- Service metadata or feature toggle: edit `codegen/src/rboto_codegen/services/*.toml`.
- Parsed descriptor fields: edit `model.py` and `registry.py`.
- Type conversion or operation behavior: edit `generator.py`.
- Repeated file structure: edit a Jinja template.
- Handwritten runtime behavior: edit the relevant non-generated Rust runtime file.

Do not add service-name branches to generic generation until a declarative customization
has been shown to be insufficient.

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

## Native CLI

`cli_generator.py` extends the same model/parser alignment used by `ServiceGenerator`.
Change CLI adapters in this generator, the `cli_*.j2` templates or service descriptors.
`generate --all` regenerates CLI and Python artifacts; `generate-cli --all` can refresh
just the CLI. `generate-manifests --all` bootstraps both workspaces before downloading
crates. Keep both Cargo lockfiles synchronized after changing pins. See ADR-0012.
