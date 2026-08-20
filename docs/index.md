# rboto

Async, strictly typed AWS clients for Python, powered by the official AWS SDK for Rust.

!!! info "Early development"
    rboto is currently an experimental project. S3, SNS, SQS, and DynamoDB are available
    as independently installable clients.

## Why rboto?

Python's AWS ecosystem is mature, but native async support often requires additional
packages and responses are commonly represented by untyped dictionaries. rboto takes a
different approach:

- **Async-only** - every AWS operation is awaitable.
- **Strictly typed** - generated method signatures cover every modeled parameter.
- **Official AWS runtime** - authentication, signing, retries, endpoints, and HTTP are
  handled by the AWS SDK for Rust.
- **Service packages** - install only the clients your application needs.
- **Native streaming** - S3 bodies remain incremental Rust streams exposed as Python
  async iterators.

## Installation

rboto supports CPython 3.12, 3.13, and 3.14 on Linux and macOS.

=== "S3"
    ```bash
    pip install "rboto[s3]"
    ```

=== "SQS"
    ```bash
    pip install "rboto[sqs]"
    ```

=== "SNS"
    ```bash
    pip install "rboto[sns]"
    ```

=== "DynamoDB"
    ```bash
    pip install "rboto[dynamodb]"
    ```

=== "All clients"
    ```bash
    pip install "rboto[all]"
    ```

## Credentials

rboto uses the standard AWS credential chain. Environment variables work without any
rboto-specific configuration:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
```

Temporary credentials can also provide `AWS_SESSION_TOKEN`.

## First request

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

Client construction performs no network I/O. AWS configuration and credentials are
loaded lazily by the Rust SDK when the first operation is awaited.

## Architecture

```text
Strict Python API
       |
       v
Generated PyO3 adapter
       |
       v
Official aws-sdk-* crate
       |
       v
AWS authentication, retries, HTTP, and protocols
```

The Python and Rust adapters are generated from release-matched Smithy models and AWS
SDK crates. Generated sources are checked into the repository, reviewed through pull
requests, and compiled without downloading models during wheel builds.

Continue to [Clients](clients.md) for S3, SNS, SQS, and DynamoDB examples.
