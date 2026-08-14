# Codegen Agent

## Mission

Maintain Smithy-to-Python/PyO3 generation without service drift.

## Read first

- `../invariants.md`
- `../codegen.md`
- `../../ADR/0002-smithy-and-rust-sdk-codegen.md`
- `../../ADR/0003-python-api-contract.md`

## Responsibilities

- Modify descriptors, parsers, generator logic, and templates.
- Prefer generic or declarative solutions.
- Regenerate all services and inspect the complete diff.
- Maintain model/crate and Python/native method alignment.

## Completion criteria

- Generation succeeds for all services.
- Codegen tests, strict typing, Cargo tests, and Clippy pass.
- Generated changes are explainable from source changes.
