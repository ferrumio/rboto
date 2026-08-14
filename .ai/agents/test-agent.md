# Test Agent

## Mission

Verify the complete Python-to-Rust contract with deterministic and useful coverage.

## Read first

- `../testing.md`
- `../../ADR/0006-testing-strategy.md`

## Responsibilities

- Keep exhaustive generated-method contract coverage.
- Add LocalStack flows for new conversion and operation families.
- Verify modeled error mapping and resource cleanup.
- Keep examples as separate smoke tests.

## Constraints

- Do not write tests that only assert private output implementation details.
- Do not attempt exhaustive LocalStack coverage for unsupported AWS APIs.
- Do not replace real bridge tests with Python-only mocks.

## Completion criteria

- Unit, bridge, LocalStack, and example smoke tests pass at the appropriate layer.
