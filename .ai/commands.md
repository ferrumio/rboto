# Canonical Commands

Run commands from the repository root unless a working directory is shown.

## Generate

```bash
.venv/bin/rboto-codegen generate --all
```

## Python quality

```bash
.venv/bin/ruff check codegen/src codegen/tests packages/rboto/src packages/rboto/tests tests examples benchmarks .github/scripts
.venv/bin/mypy --strict codegen/src codegen/tests packages/rboto/src packages/rboto-*/python tests/typecheck examples
.venv/bin/pyright
.venv/bin/pytest codegen/tests packages/rboto/tests -q
```

## Rust quality

```bash
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

## Native CLI quality

The CLI is a separate workspace. Keep one local Cargo job and disable incremental
compilation. On constrained hosts, apply process-group CPU/RAM/time limits and monitor
the target cache. Do not run simultaneous native builds.

```bash
cargo fmt --manifest-path cli/Cargo.toml --all -- --check
CARGO_BUILD_JOBS=1 cargo test --manifest-path cli/Cargo.toml -p rboto-cli-core --locked
CARGO_BUILD_JOBS=1 cargo clippy --manifest-path cli/Cargo.toml --workspace --all-targets --locked -- -D warnings
CARGO_BUILD_JOBS=1 cargo build --manifest-path cli/Cargo.toml --release --locked
RBOTO_CLI_BINARY=cli/target/release/rboto .venv/bin/pytest tests/cli -q
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
.venv/bin/pytest tests/integration packages/rboto-s3/tests packages/rboto-sns/tests packages/rboto-sqs/tests packages/rboto-dynamodb/tests packages/rboto/tests -q
```

## Documentation

```bash
.venv/bin/zensical build --clean
```
