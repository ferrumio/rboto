# ADR-0006: Test generated contracts and representative AWS flows

- Status: Accepted
- Date: 2026-08-14
- Deciders: rboto maintainers

## Context

Testing every AWS operation against LocalStack is slow and unreliable because many APIs
require complex infrastructure or are not implemented by LocalStack. Testing only a few
operations would fail to detect missing generated Python or PyO3 methods.

## Decision

Testing is layered:

1. Codegen tests validate model-to-crate alignment and generator configuration.
2. A compiled bridge contract test validates every generated Python method, every native
   PyO3 counterpart, and Python-to-native parameter dispatch.
3. LocalStack tests exercise representative operation families through the complete path:
   Python wrapper, PyO3, Tokio, AWS Rust SDK, and HTTP service.
4. Public examples run as independent LocalStack smoke tests.
5. Strict typing, linting, Rust tests, Clippy, and formatting are required quality gates.

The method-count contract is currently 106 S3, 42 SNS, 23 SQS, and 57 DynamoDB operations.

## Consequences

- All generated method names and dispatch paths receive deterministic coverage.
- Functional coverage focuses on behavior rather than exhaustive LocalStack emulation.
- Integration tests require compiled native extensions.
- Examples remain executable documentation and an additional smoke boundary.

## Alternatives considered

- Run every operation against LocalStack: rejected because support and setup are incomplete.
- Mock only the Python facade: rejected because it would not prove PyO3 registration.
- Test only examples: rejected because examples intentionally cover a small API subset.

## References

- `codegen/tests/`
- `tests/integration/test_generated_client_bridge.py`
- `tests/integration/test_s3_client_methods.py`
- `tests/integration/test_sns_client_methods.py`
- `tests/integration/test_sqs_client_methods.py`
- `tests/integration/test_dynamodb_client_methods.py`
