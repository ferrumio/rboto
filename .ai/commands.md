# Canonical Commands

Run commands from the repository root unless a working directory is shown.

## Generate

```bash
.venv/bin/rboto-codegen generate --all
```

## Python quality

Install `pydantic>=2,<3` in the development environment to check the optional
integration examples and run the compiled model interoperability tests.
Install `types-jmespath>=1,<2` for strict checking of the codegen-only expression parser.
Install `packages/rboto[opentelemetry]` and `opentelemetry-sdk>=1.39,<2` for
the optional integration tests and typed examples.

```bash
.venv/bin/ruff check codegen/src codegen/tests packages/rboto/src packages/rboto/tests tests examples benchmarks .github/scripts evals/rboto_evals evals/tests evals/reference_solutions
.venv/bin/mypy --strict codegen/src codegen/tests packages/rboto/src packages/rboto-*/python tests/typecheck examples .github/scripts evals/rboto_evals evals/tests evals/reference_solutions
.venv/bin/pyright
.venv/bin/pytest codegen/tests packages/rboto/tests -q
```

Install `evals` editable for the private evaluation tools and pinned grading
dependencies. Runner tests make no model calls; grader fixtures require the four
installed native clients (`s3`, `dynamodb`, `sqs`, `bedrock-runtime`).

```bash
.venv/bin/pip install -e evals
.venv/bin/pytest evals/tests/test_runner.py -q
RBOTO_TEST_SERVICES=s3,dynamodb,sqs,bedrock-runtime .venv/bin/pytest evals/tests/test_grading.py -q
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
.venv/bin/pytest tests/integration packages/rboto-s3/tests packages/rboto-sns/tests packages/rboto-sqs/tests packages/rboto-dynamodb/tests packages/rboto/tests -q
```

## Documentation

Install `codegen` and `zensical`. Fetch the pinned models and Rust crate sources;
documentation generation does not compile native extensions.

```bash
cargo fetch --locked
.venv/bin/rboto-codegen fetch-model --all
.venv/bin/python -m rboto_codegen.reference_site
.venv/bin/zensical build --clean --strict
```
