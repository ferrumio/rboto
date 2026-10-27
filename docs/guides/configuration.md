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

Omitted settings use the Rust SDK's resolution rules. Set a region explicitly when
your application needs a particular region.

## Profiles and SSO

rboto uses the shared files, including `~/.aws/config` and `~/.aws/credentials`.
Pass `profile="development"` or set `AWS_PROFILE`.

For SSO, run `aws sso login --profile development` before starting the application.
rboto uses the available session; it does not provide interactive login.

Roles configured with `role_arn` and `source_profile` in a shared profile use the
SDK's profile provider. The current constructors do not accept role or access-key
arguments directly. Profiles can also use `credential_process`.

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

The current constructors do not expose connection/read timeouts. An
[asyncio deadline](async-only.md#deadlines) bounds the application's wait.

## Local endpoints

```python
from rboto import dynamodb

client = dynamodb(region="us-east-1", endpoint_url="http://localhost:4566")
```

Endpoint overrides still use the credential chain. Supply test credentials in
the test process when the local service expects signed requests. Remote HTTPS
endpoints require trusted certificates; there is no `verify=False` option.
