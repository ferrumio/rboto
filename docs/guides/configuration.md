# Configuration and credentials

Create a client with the region and profile you need, then reuse it:

```python
from rboto import S3Client

client = S3Client(region="us-east-1", profile="development")
```

Each client initializes its native service client on first use and keeps it for
subsequent operations.

## Constructor settings

| Constructor argument | Purpose |
|---|---|
| `region` | Explicit service region |
| `profile` | Named shared configuration/credential profile |
| `endpoint_url` | Override the service endpoint |
| `session` | Reuse settings from an `rboto.Session` |
| `config` | Credentials, roles, timeouts, retries, proxy, and TLS trust |
| `interceptors` | Operation middleware; see [interceptors](interceptors.md) |

Omitted settings use the Rust SDK's resolution rules. Set a region explicitly when
your application needs a particular region.

## Profiles and SSO

rboto uses the shared files, including `~/.aws/config` and `~/.aws/credentials`.
Pass `profile="development"` or set `AWS_PROFILE`.

For SSO, run `aws sso login --profile development` before starting the application.
rboto uses the available session; it does not provide interactive login.

Roles configured with `role_arn` and `source_profile` in a shared profile use the
SDK's profile provider. Profiles can also use `credential_process`.

To assume a role from an explicit source profile:

```python
from rboto import ClientConfig, S3Client

client = S3Client(
    region="us-east-1",
    profile="development",
    config=ClientConfig(
        role_arn="arn:aws:iam::123456789012:role/application",
        role_session_name="application",
    ),
)
```

`ClientConfig` also accepts `access_key_id`, `secret_access_key`, and an optional
`session_token`. Supply the key and secret together. Explicit credentials take
precedence over profile credentials; if `role_arn` is also set, they authenticate
the AssumeRole call. `external_id` and `role_session_name` require `role_arn`.

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
from rboto import Session, S3Client, SQSClient

session = Session(region="us-east-1", profile="development")
objects = S3Client(session=session)
messages = SQSClient(session=session)
```

Pass `config=ClientConfig(...)` to a Session to reuse these settings too.
`ClientConfig` and `Session` are immutable. Each client initializes its own
SDK configuration; the session does not share a credential cache or connection
pool across services. Combining `session` with explicit constructor settings raises
`ValueError`. Factories such as `rboto.s3()` accept the same settings for compatibility.

## Retries and timeouts

`AWS_MAX_ATTEMPTS` sets total attempts, including the initial request.
`AWS_RETRY_MODE` selects `standard` or `adaptive`.

```bash
export AWS_MAX_ATTEMPTS=3
export AWS_RETRY_MODE=standard
```

Per-client values override the corresponding environment/profile settings:

```python
from rboto import ClientConfig, S3Client

client = S3Client(
    region="us-east-1",
    config=ClientConfig(
        connect_timeout=2,
        read_timeout=10,
        attempt_timeout=15,
        operation_timeout=30,
        max_attempts=3,
        retry_mode="standard",
    ),
)
```

Timeouts are positive, finite seconds. `operation_timeout` covers the SDK
operation, including retries; `attempt_timeout` limits an individual attempt.
They do not bound configuration loading or subsequent reads of a returned stream.
Use an [asyncio deadline](async-only.md#deadlines) around the whole operation and
stream consumption when you need an application deadline.

## Proxies and private certificates

```python
from rboto import ClientConfig, S3Client

client = S3Client(
    region="us-east-1",
    config=ClientConfig(
        proxy_url="http://proxy.internal:8080",
        no_proxy="localhost,127.0.0.1,.internal.example",
        ca_bundle="/etc/application/company-ca.pem",
    ),
)
```

`proxy_url` supports HTTP or HTTPS proxies and applies to both HTTP and HTTPS
destinations. For basic authentication, include URL-encoded credentials in the
URL. `no_proxy` is a comma-separated list of hosts, domains, or IP ranges that
connect directly; it requires `proxy_url`.

`ca_bundle` adds PEM certificates to the system trust roots. It applies to service
and credential-provider connections, including AssumeRole and HTTPS proxies.
The file is read when the client first initializes; create a new client after
rotating the bundle. Missing or invalid bundles raise `ConfigurationError`.

These settings belong to the client and do not change the process environment.
An explicitly configured transport uses only its own proxy rules, not
`HTTP_PROXY`, `HTTPS_PROXY`, or `NO_PROXY`. Proxy URLs and credentials are omitted
from the configuration's representation. TLS certificate and hostname checks
remain enabled.

## Local endpoints

```python
from rboto import DynamoDBClient

client = DynamoDBClient(region="us-east-1", endpoint_url="http://localhost:4566")
```

Endpoint overrides still use the credential chain. Supply test credentials in
the test process when the local service expects signed requests. Remote HTTPS
endpoints require trusted certificates; there is no `verify=False` option.
