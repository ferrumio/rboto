# rboto 🐍🦀

[![CI](https://github.com/ferrumio/rboto/actions/workflows/ci.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/ci.yml)
[![Integration](https://github.com/ferrumio/rboto/actions/workflows/integration.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/integration.yml)
[![Documentation](https://github.com/ferrumio/rboto/actions/workflows/docs.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/docs.yml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](#license)

Async, strictly typed AWS clients for Python, powered by the official AWS SDK for Rust.

> **Early development:** rboto is experimental and its API may change before 1.0. S3,
> SQS, and DynamoDB are available as independently installable clients.

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

| Service | Operations | Package | Highlights |
|---------|-----------:|---------|------------|
| Amazon S3 | 106 | `rboto-s3` | Streaming bodies, multipart operations, event streams |
| Amazon SQS | 23 | `rboto-sqs` | Messages, batches, queues, tags, and permissions |
| Amazon DynamoDB | 57 | `rboto-dynamodb` | Items, queries, batches, transactions, and streams |

The public Python API and PyO3 bridge are generated from release-matched Smithy models and
pinned AWS Rust SDK crates.

## Installation

rboto supports CPython 3.12, 3.13, and 3.14 on Linux and macOS.

Install one service:

```bash
pip install "rboto[s3]"
pip install "rboto[sqs]"
pip install "rboto[dynamodb]"
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

Requirements: Python 3.12+, Rust, and a C compiler supported by PyO3.

```bash
git clone https://github.com/ferrumio/rboto.git
cd rboto

python3 -m venv .venv
.venv/bin/pip install maturin pytest mypy pyright ruff
.venv/bin/pip install -e codegen -e packages/rboto

(cd packages/rboto-s3 && ../../.venv/bin/maturin develop --locked)
(cd packages/rboto-sqs && ../../.venv/bin/maturin develop --locked)
(cd packages/rboto-dynamodb && ../../.venv/bin/maturin develop --locked)

.venv/bin/pytest codegen/tests packages/rboto/tests -q
```

See [`.ai/commands.md`](.ai/commands.md) for the complete generation and verification
workflow.

## License

Apache-2.0
