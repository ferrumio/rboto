# rboto PoC

rboto is an async-only, strictly typed Python adapter over the official AWS SDK for
Rust. This PoC starts with an independently packaged S3 client and a Python-based,
service-oriented codegen foundation.

The service packages are generated from release-matched Smithy models and pinned AWS
SDK crates:

- S3: 106 operations from `aws-sdk-s3 1.120.0`
- SQS: 23 operations from `aws-sdk-sqs 1.92.0`
- DynamoDB: 57 operations from `aws-sdk-dynamodb 1.102.0`

DynamoDB currently exposes the modeled `AttributeValue` tagged union. Native Python
value inference is intentionally deferred to a DynamoDB customization layer.

The authoritative design is in `../final_decisions/`.

## Repository Layout

```text
packages/rboto/       Python facade and shared exception types
packages/rboto-s3/    Python package for the native S3 extension
crates/rboto-core/    Internal Rust helpers, never published to crates.io
crates/rboto-s3/      Async PyO3 adapter over aws-sdk-s3
codegen/              Extensible Python code generator foundation
tests/typecheck/      Strict typing fixtures
```

Each service is introduced by one TOML file under
`codegen/src/rboto_codegen/services/`. The core generator contains no S3 or SQS names.

```bash
.venv/bin/rboto-codegen fetch-model s3
.venv/bin/rboto-codegen report s3
.venv/bin/rboto-codegen generate s3
.venv/bin/rboto-codegen generate sqs
.venv/bin/rboto-codegen generate dynamodb
```

## Local Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e packages/rboto
.venv/bin/pip install -e codegen
(cd packages/rboto-s3 && ../../.venv/bin/maturin develop)
.venv/bin/python -m pytest packages/rboto/tests codegen/tests
```

The native client performs no network or credential loading in its constructor. AWS
configuration is loaded lazily inside the first awaited operation.
