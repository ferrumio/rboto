# Configuration and credentials

Create a client with the region and profile you need, then reuse it:

```python
from rboto import s3

client = s3(region="us-east-1", profile="development")
```

Each client initializes its native service client on first use and keeps it for
subsequent operations.

## Constructor settings

| Factory argument | Purpose |
|---|---|
| `region` | Explicit service region |
| `profile` | Named shared configuration/credential profile |
| `endpoint_url` | Override the service endpoint |
| `session` | Reuse settings from an `rboto.Session` |
| `config` | Credential, AssumeRole, timeout, and retry settings in a `ClientConfig` |

Omitted settings use the Rust SDK's resolution rules. Set a region explicitly when
your application needs a particular region.

## Profiles and SSO

rboto uses the shared files, including `~/.aws/config` and `~/.aws/credentials`.
Pass `profile="development"` or set `AWS_PROFILE`.

For SSO, run `aws sso login --profile development` before starting the application.
rboto uses the available session; it does not provide interactive login.

Roles configured with `role_arn` and `source_profile` in a shared profile use the
SDK's profile provider. Use `ClientConfig` for explicit credentials or role settings (see below).
Profiles can also use `credential_process`.

## Environment and workload credentials

The default chain checks environment credentials, shared profiles, web identity,
container credentials, and EC2 instance metadata.

| Variable | Purpose |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Credential pair |
| `AWS_SESSION_TOKEN` | Token accompanying temporary credentials |
| `AWS_PROFILE` | Shared profile selection |
| `AWS_REGION` | Region selection |
| `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE` | Alternative shared files |

The process must receive these variables; rboto does not load `.env` files.
For workload roles, configure the platform to supply its credential source.
Refreshable providers handle credential renewal through the SDK.

## Share settings with a Session

```python
from rboto import Session, s3, sqs

session = Session(region="us-east-1", profile="development")
objects = s3(session=session)
messages = sqs(session=session)
```

`Session` is an immutable collection of settings. Each client initializes its own
SDK configuration; the session does not share a credential cache or connection
pool across services. Combining `session` with explicit factory settings raises
`ValueError`.

## Retries and timeouts

`AWS_MAX_ATTEMPTS` sets total attempts, including the initial request.
`AWS_RETRY_MODE` selects `standard` or `adaptive`.

```bash
export AWS_MAX_ATTEMPTS=3
export AWS_RETRY_MODE=standard
```

Pass a `ClientConfig` to configure timeouts and retries per client. An
[asyncio deadline](async-only.md#deadlines) can include configuration loading and
stream consumption as well.

## Local endpoints

```python
from rboto import dynamodb

client = dynamodb(region="us-east-1", endpoint_url="http://localhost:4566")
```

Endpoint overrides still use the credential chain. Supply test credentials in
the test process when the local service expects signed requests. Remote HTTPS
endpoints require trusted certificates; there is no `verify=False` option.

## Request settings

Pass `ClientConfig` to any factory or client constructor:

```python
from rboto import ClientConfig, Session, s3, sts

settings = ClientConfig(
    connect_timeout=2,
    read_timeout=15,
    attempt_timeout=20,
    operation_timeout=45,
    max_attempts=3,
)
session = Session(region="us-east-1", profile="development", config=settings)
identity = sts(session=session)
objects = s3(session=session)
```

These settings are immutable. `Session` forwards them to each client; each client
keeps its own native configuration and credential cache.


## Credentials and AssumeRole

By default, the SDK uses its credential chain. For credentials supplied by your
application, set `access_key_id` and `secret_access_key` together, with an optional
`session_token`. Explicit credentials are static values; use a refreshable profile
or workload provider when credentials need automatic renewal.

To assume a role with a named source profile:

```python
from rboto import ClientConfig, sts

client = sts(
    region="us-east-1",
    profile="source-account",
    config=ClientConfig(
        role_arn="arn:aws:iam::123456789012:role/application",
        role_session_name="application",
        external_id="external-id-provided-by-role-owner",
    ),
)
```

AssumeRole uses the selected profile's source credentials, or explicit credentials
if supplied. Region and configured request limits also apply to the STS call.
Credentials load on the first operation, and refreshable providers handle renewal.

## Timeouts and attempts

| Setting | Meaning |
|---|---|
| `connect_timeout` | Establishing a connection, in seconds |
| `read_timeout` | Waiting for the HTTP response headers, in seconds |
| `attempt_timeout` | One SDK request attempt |
| `operation_timeout` | SDK operation including its retry attempts |
| `max_attempts` | Total attempts, including the initial request |
| `retry_mode` | `standard` or `adaptive` |

Only supplied settings override SDK defaults. Timeouts must be finite and positive.
`max_attempts` must be at least one. Invalid combinations raise `ConfigurationError`
before network I/O.

Operation timeouts do not include configuration loading or application reads from
returned streams. Use `asyncio.timeout()` around the call and iteration for an
application deadline. A timed-out write can still have reached the service.

