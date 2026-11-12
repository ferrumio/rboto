# Testing Guide

## Layers

- Codegen tests check supported parser syntax, semantic model/crate alignment and
  configuration behavior. Mutation fixtures verify that drift blocks generation.
- `test_generated_client_bridge.py` discovers every registered service and proves every
  Python/PyO3 method and dispatch path against the pinned Rust operations.
  Dispatch uses a recording replacement; it does not exercise serialization for
  every method. Native registration and property checks require compiled wheels.
- CI wheel jobs run the compiled contract for each service. `RBOTO_TEST_SERVICES` may
  select installed services for a targeted run; leaving it unset requires all services.
- Service method tests call LocalStack through the real Rust SDK.
- `test_new_client_protocols.py` uses compiled clients and controlled HTTP peers
  to assert encoded requests and decoded outputs for representative protocols.
  Error, stream and lifecycle suites inject failures across the same boundary.
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

The generated-source CI job regenerates the complete catalog twice and requires
both passes to match committed files. See ADR-0006 and ADR-0033.

## Application code evaluations

`evals/tests/test_runner.py` exercises budgets, pairing, and reporting without a
model. `evals/tests/test_grading.py` validates trusted reference solutions and
incorrect variants through installed native clients and controlled HTTP peers.
It honors `RBOTO_TEST_SERVICES` in wheel jobs. CI never calls a paid model.

Follow `evals/README.md` for opt-in comparisons. Only execute model-produced code
with the Docker grader. Keep solutions and private grading cases out of model
inputs. This five-task pilot measures supplied documentation, not shell-agent
tool use or general AWS compatibility; see ADR-0035.

Start reviews with `evals/results/README.md`. Read individual records from the
JSON/JSONL evidence only when investigating a result; do not load all model
responses into routine agent context.
