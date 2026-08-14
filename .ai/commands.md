# Canonical Commands

Run commands from the repository root unless a working directory is shown.

## Generate

```bash
.venv/bin/rboto-codegen generate --all
```

## Python quality

```bash
.venv/bin/ruff check codegen/src codegen/tests packages/rboto/src packages/rboto/tests tests examples benchmarks .github/scripts
.venv/bin/mypy --strict codegen/src codegen/tests packages/rboto/src packages/rboto-s3/python packages/rboto-sqs/python packages/rboto-dynamodb/python tests/typecheck examples
.venv/bin/pyright
.venv/bin/pytest codegen/tests packages/rboto/tests -q
```

## Rust quality

```bash
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

## Build editable native packages

Run from each `packages/rboto-<service>` directory:

```bash
../../.venv/bin/maturin develop --locked
```

Use `--release` only when performance or release packaging is being validated.

## LocalStack tests

Required environment:

```bash
export AWS_ACCESS_KEY_ID=testing
export AWS_SECRET_ACCESS_KEY=testing
export AWS_REGION=us-east-1
export AWS_ENDPOINT_URL=http://localhost:4566
```

Run:

```bash
.venv/bin/pytest tests/integration packages/rboto-s3/tests packages/rboto-sqs/tests packages/rboto-dynamodb/tests packages/rboto/tests -q
```

## Documentation

```bash
.venv/bin/zensical build --clean
```
