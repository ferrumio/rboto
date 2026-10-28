# Getting started

Install the STS client and identify the credentials your application is using.

## Install from source

rboto packages are not published on PyPI yet. Use a checkout with CPython
3.12–3.15, Rust 1.94.1 or newer, and native build tools (a C compiler and CMake).

```bash
git clone https://github.com/ferrumio/rboto.git
cd rboto
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./packages/rboto ./packages/rboto-sts
```

This builds STS only. Add services by installing their directories:

```bash
python -m pip install ./packages/rboto ./packages/rboto-s3 ./packages/rboto-dynamodb
```

Use packages from the same checkout. Each service requires the matching facade
version. Installing `rboto` alone does not install a service client.

### Package names and wheel targets

Services are packaged as `rboto-<service>`. The facade defines extras such as
`rboto[s3,dynamodb]` for installing matching versions once packages are published.

The release configuration targets these CPython wheels:

| Operating system | Architecture | Python |
|---|---|---|
| Linux with glibc | x86_64, aarch64 | 3.12, 3.13, 3.14, 3.15 |
| macOS | x86_64, arm64 | 3.12, 3.13, 3.14, 3.15 |

Windows, musllinux/Alpine, PyPy, and free-threaded CPython are not in the current
wheel matrix. Wheels target individual Python versions.

## Select credentials

For an existing named profile:

```bash
export AWS_PROFILE=development
export AWS_REGION=us-east-1
```

For SSO, establish the login session first:

```bash
aws sso login --profile development
```

Applications using instance or container roles can use the credentials supplied by
the environment. See [Configuration](guides/configuration.md) for other sources.

## Make a request

Save this as `identity.py`:

```python
--8<-- "examples/docs/first_request.py"
```

Run `python identity.py` in the environment where you installed the client.
The output contains the account ID and principal ARN returned by STS.

Client construction is synchronous and performs no network request. The operation
runs when awaited. If Python cannot import `rboto_sts`, install that service in the
same environment. See [Errors](guides/errors.md) for authentication and service failures.

Choose your next client from the [catalog](clients.md).
