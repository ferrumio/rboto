# rboto 🐍🦀

[![CI](https://github.com/ferrumio/rboto/actions/workflows/ci.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/ci.yml)
[![Integration](https://github.com/ferrumio/rboto/actions/workflows/integration.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/integration.yml)
[![Documentation](https://github.com/ferrumio/rboto/actions/workflows/docs.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/docs.yml)
[![CodeQL](https://github.com/ferrumio/rboto/actions/workflows/codeql.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ferrumio/rboto/badge)](https://scorecard.dev/viewer/?uri=github.com/ferrumio/rboto)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](https://www.python.org/)
[![Typing](https://img.shields.io/badge/typing-strict-blue)](docs/clients.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

rboto provides typed, async-only Python clients for 34 services. Install your
clients, await operations, and read typed response attributes.

The project is under development. Packages are not published on PyPI yet.
[Getting started](docs/getting-started.md) covers source installation.

```python
import asyncio

from rboto import STSClient


async def main() -> None:
    client = STSClient(region="us-east-1")
    response = await client.get_caller_identity()
    print(response.account)
    print(response.arn)


if __name__ == "__main__":
    asyncio.run(main())
```

This STS request prints the account and principal ARN for your configured credentials.
In an async application, await the operation in the existing event loop.

- [Client catalog](docs/clients.md): packages, imports, and operations.
- [Async-only](docs/guides/async-only.md): scripts, applications, concurrency, deadlines.
- [Configuration](docs/guides/configuration.md): credentials, profiles, regions, sessions.
- [Types and responses](docs/guides/types.md): typed inputs and response attributes.
- [Streaming](docs/guides/streaming.md): byte streams and event streams.
- [Errors](docs/guides/errors.md): service exceptions and partial failures.

Read the [documentation](https://ferrumio.github.io/rboto) for service examples.
Signing, credentials, retries, and HTTP use the SDK for Rust.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), [GOVERNANCE.md](GOVERNANCE.md),
[SECURITY.md](SECURITY.md), and the [Code of conduct](CODE_OF_CONDUCT.md).
Generation and verification commands are in [.ai/commands.md](.ai/commands.md).
Architecture decisions live in [ADR/](ADR/README.md).

## License

Apache-2.0
