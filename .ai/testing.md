# Testing Guide

## Layers

- Codegen tests prove model/crate alignment and configuration behavior.
- `test_generated_client_bridge.py` proves all 186 Python/PyO3 methods and dispatch paths.
- Service method tests call LocalStack through the real Rust SDK.
- Package tests cover factories and shared exception behavior.
- Examples are independent smoke tests and must remain executable.

## Functional test rules

- Pass `endpoint_url` explicitly to client factories.
- Use unique resource names.
- Clean resources in `finally` blocks.
- Test behavior across the Python-to-Rust boundary, not implementation-only output classes.
- Include representative modeled failures.
- Avoid APIs that LocalStack does not implement reliably unless the test is explicitly
  conditional.
- Do not claim AWS compatibility from LocalStack alone.

## Adding a generated operation

The exhaustive contract test should discover it automatically. Add a LocalStack test only
when the operation introduces a new conversion, ownership model, error path, or operation
family that existing tests do not exercise.

See ADR-0006.
