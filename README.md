# rboto 🐍🦀

[![CI](https://github.com/ferrumio/rboto/actions/workflows/ci.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/ci.yml)
[![Integration](https://github.com/ferrumio/rboto/actions/workflows/integration.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/integration.yml)
[![Documentation](https://github.com/ferrumio/rboto/actions/workflows/docs.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/docs.yml)
[![CodeQL](https://github.com/ferrumio/rboto/actions/workflows/codeql.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ferrumio/rboto/badge)](https://scorecard.dev/viewer/?uri=github.com/ferrumio/rboto)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](https://www.python.org/)
[![Typing](https://img.shields.io/badge/typing-strict-blue)](docs/clients.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

Async, strictly typed AWS clients for Python, powered by the official AWS SDK for Rust.

> **Early development:** rboto is experimental and its API may change before 1.0.
> 34 AWS service clients are available as independently installable packages.

## Why rboto?

Python has an excellent AWS ecosystem, but native async support often requires additional
layers and responses are commonly exposed as untyped dictionaries. rboto takes a different
approach:

- **Async-only** - every network operation is awaitable and runs through Tokio.
- **Strictly typed** - generated signatures cover modeled parameters, literals, and shapes.
- **Rust-powered** - signing, credentials, retries, endpoints, and HTTP use the official AWS
  SDK for Rust.
- **Typed responses** - immutable native objects expose properties such as
  `response.message_id`, with `to_dict()` available when needed.
- **Native streaming** - S3 response bodies remain incremental Rust streams exposed as
  Python async iterators.
- **Install only what you use** - every AWS service ships as an independent native wheel.

## Supported services

The catalog provides **34 AWS service clients and 1,865 operations**, including
EventBridge, Scheduler, Pipes, CloudWatch Logs, Systems Manager, KMS, Step Functions,
and SES v2.

| Service / extra | Factory | Operations |
|---|---|---:|
| `api-gateway` | `api_gateway()` | 124 |
| `bedrock` | `bedrock()` | 108 |
| `bedrock-agent` | `bedrock_agent()` | 86 |
| `bedrock-agent-runtime` | `bedrock_agent_runtime()` | 35 |
| `bedrock-agentcore` | `bedrock_agentcore()` | 67 |
| `bedrock-agentcore-control` | `bedrock_agentcore_control()` | 171 |
| `bedrock-data-automation` | `bedrock_data_automation()` | 27 |
| `bedrock-data-automation-runtime` | `bedrock_data_automation_runtime()` | 6 |
| `bedrock-runtime` | `bedrock_runtime()` | 11 |
| `cloudtrail` | `cloudtrail()` | 60 |
| `cloudwatch-logs` | `cloudwatch_logs()` | 118 |
| `cognito-identity` | `cognito_identity()` | 23 |
| `connecthealth` | `connecthealth()` | 16 |
| `dynamodb` | `dynamodb()` | 58 |
| `eventbridge` | `eventbridge()` | 57 |
| `guardduty` | `guardduty()` | 102 |
| `kms` | `kms()` | 54 |
| `lambda` | `lambda_()` | 88 |
| `lex-runtime-v2` | `lex_runtime_v2()` | 6 |
| `pipes` | `pipes()` | 10 |
| `polly` | `polly()` | 10 |
| `qbusiness` | `qbusiness()` | 83 |
| `s3` | `s3()` | 112 |
| `sagemaker-runtime-http2` | `sagemaker_runtime_http2()` | 1 |
| `scheduler` | `scheduler()` | 12 |
| `secrets-manager` | `secrets_manager()` | 23 |
| `sesv2` | `sesv2()` | 116 |
| `sfn` | `sfn()` | 37 |
| `signin` | `signin()` | 11 |
| `sns` | `sns()` | 42 |
| `sqs` | `sqs()` | 23 |
| `ssm` | `ssm()` | 152 |
| `sts` | `sts()` | 11 |
| `transcribe-streaming` | `transcribe_streaming()` | 5 |

Each service is packaged as `rboto-<service>`. The public Python API and PyO3 bridge
are generated from release-matched Smithy models and pinned AWS Rust SDK crates.

## Installation

rboto supports CPython 3.12, 3.13, 3.14, and 3.15 on Linux and macOS.

Install one service:

```bash
pip install "rboto[s3]"
pip install "rboto[sns]"
pip install "rboto[sqs]"
pip install "rboto[dynamodb]"
pip install "rboto[bedrock-runtime,secrets-manager,sts]"
pip install "rboto[cloudwatch-logs,ssm,kms,sfn,sesv2]"
pip install "rboto[eventbridge,scheduler,pipes]"
```

Or install every available client:

```bash
pip install "rboto[all]"
```

## Quick start

### List S3 buckets

```python
import asyncio

from rboto import s3


async def main() -> None:
    client = s3(region="us-east-1")
    response = await client.list_buckets()

    for bucket in response.buckets or []:
        print(bucket.name)


asyncio.run(main())
```

Client construction performs no network I/O. Credentials and AWS configuration are loaded
lazily when the first operation is awaited.

### Send and receive an SQS message

```python
from rboto import sqs

client = sqs(region="us-east-1")

sent = await client.send_message(
    queue_url=queue_url,
    message_body="hello from rboto",
)
print(sent.message_id)

received = await client.receive_message(
    queue_url=queue_url,
    max_number_of_messages=10,
    wait_time_seconds=10,
)

for message in received.messages or []:
    print(message.body)
```

### Put and get a DynamoDB item

DynamoDB uses an explicit, strictly typed `AttributeValue` tagged union:

```python
from rboto import dynamodb
from rboto_dynamodb.types import AttributeValue

client = dynamodb(region="us-east-1")

item: dict[str, AttributeValue] = {
    "pk": {"s": "USER#1"},
    "name": {"s": "Ada"},
    "active": {"bool": True},
}

await client.put_item(table_name="users", item=item)

response = await client.get_item(
    table_name="users",
    key={"pk": {"s": "USER#1"}},
)
print(response.item)
```

## Stream S3 objects

S3 bodies are not eagerly copied into Python. Consume chunks as they arrive:

```python
response = await client.get_object(
    bucket="my-bucket",
    key="large-file.bin",
)

async for chunk in response.body:
    process(chunk)
```

Or aggregate the remaining stream when the object is known to fit in memory:

```python
data: bytes = await response.body.read()
```

Independent response bodies can be consumed concurrently with `asyncio.gather()`.

## AWS credentials

rboto uses the standard AWS credential chain supported by the AWS SDK for Rust. Environment
variables work without rboto-specific configuration:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
```

Profiles, temporary credentials, and `AWS_SESSION_TOKEN` are also supported by the SDK
configuration chain.

## How it works

```text
Strict Python API
       |
       v
Generated Python wrapper
       |
       v
Generated PyO3 adapter
       |
       v
Official aws-sdk-* Rust crate
       |
       v
AWS
```

Each service is packaged independently, while the small `rboto` facade provides sessions,
shared exceptions, and convenient factories. Generated sources are committed so changes
can be reviewed and wheel builds do not need to download service models.

## Documentation

Full documentation: [https://ferrumio.github.io/rboto](https://ferrumio.github.io/rboto)

More examples are available in [`examples/`](examples/) and the service guide is available
in [`docs/clients.md`](docs/clients.md).

## GenAI contributions

AI tools can accelerate development, but generated changes still require engineering
judgment and complete verification.

This repository includes:

- [`AGENTS.md`](AGENTS.md) - the entry point for coding agents.
- [`.ai/`](.ai/) - architecture, invariants, commands, and specialized agent guidance.
- [`ADR/`](ADR/) - internal Architecture Decision Records explaining why the project works
  this way.

If you contribute with AI assistance, understand the generated code, follow the project
patterns, and run the required Python, Rust, bridge, and LocalStack checks.

## Building from source

Requirements: Python 3.12–3.15, Rust 1.94.1+ with rustfmt, maturin 1.15+, and a C compiler
supported by PyO3.

```bash
git clone https://github.com/ferrumio/rboto.git
cd rboto

python3 -m venv .venv
source .venv/bin/activate
.venv/bin/pip install "maturin>=1.15,<2" pytest pytest-asyncio mypy pyright ruff
.venv/bin/pip install -e codegen -e packages/rboto

cargo fetch --locked
.venv/bin/rboto-codegen fetch-model --all
for service in $(.venv/bin/rboto-codegen list); do
    (cd "packages/rboto-$service" && ../../.venv/bin/maturin develop --locked)
done

.venv/bin/pytest codegen/tests packages/rboto/tests -q
.venv/bin/pytest tests/integration/test_generated_client_bridge.py \
    tests/integration/test_new_client_protocols.py -q
```

See [`.ai/commands.md`](.ai/commands.md) for the complete generation and verification
workflow.

## Contributing and governance

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and review requirements,
[GOVERNANCE.md](GOVERNANCE.md) for maintainer responsibilities and decisions, and
[SECURITY.md](SECURITY.md) for private vulnerability reporting. Community participation
follows our [Code of conduct](CODE_OF_CONDUCT.md).

## License

Apache-2.0
