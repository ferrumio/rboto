# Python API Agent

## Mission

Keep the generated Python API strict, ergonomic, and aligned with native behavior.

## Read first

- `../invariants.md`
- `../../ADR/0003-python-api-contract.md`
- `../testing.md`

## Responsibilities

- Preserve keyword-only async methods and strict annotations.
- Maintain `TypedDict`, `Literal`, aliases, and tagged input unions.
- Keep native properties, stubs, exports, and `to_dict()` aligned.
- Update typecheck fixtures, examples, and public docs when the user API changes.

## Completion criteria

- Ruff, mypy strict, and Pyright pass.
- Generated Python and PyO3 methods remain one-to-one.
- Examples use the supported public API rather than internal modules.
