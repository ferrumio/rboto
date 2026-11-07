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

Omitted settings use the Rust SDK's resolution rules. The configuration is resolved
when the client first initializes and is then reused. Set a region explicitly when
your application needs a particular region.

| Setting | Precedence, highest first |
|---|---|
| Region | Constructor `region`, `AWS_REGION`, `AWS_DEFAULT_REGION`, selected profile, instance metadata |
| Profile | Constructor `profile`, `AWS_PROFILE`, `default` |
| Signing credentials | Explicit `ClientConfig` credentials, environment credentials, profile, web identity, container credentials, instance metadata |
| Endpoint | Constructor `endpoint_url`, service-specific environment endpoint, global environment endpoint, configured profile endpoints, service default |
| Retry settings | Explicit corresponding `ClientConfig` field, environment, profile, SDK default |
| Additional CA trust | Explicit `ClientConfig.ca_bundle`, `AWS_CA_BUNDLE` |
| Proxy | Explicit `ClientConfig.proxy_url` and its bypass rules; otherwise environment proxy settings |

Selecting a profile does not remove environment credentials from the default
chain. Explicit `ClientConfig.role_arn` wraps the resolved source credentials with
AssumeRole. A missing region raises `ConfigurationError`.

## Profiles, SSO and console login

rboto uses the shared files, including `~/.aws/config` and `~/.aws/credentials`.
Pass `profile="development"` or set `AWS_PROFILE`.

For SSO, run `aws sso login --profile development` before starting the application.
rboto uses the available session; it does not provide interactive login.

Profiles created by `aws login --profile development` are also supported. The SDK
loads their `login_session` cache and refreshes credentials when the session permits
it. `AWS_LOGIN_CACHE_DIRECTORY` overrides the default `~/.aws/login/cache` location.
An expired session that cannot be renewed requires another CLI login; rboto reports
`CredentialsError` and does not open a browser or prompt.

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

Explicit credentials are static, including a supplied `session_token`. rboto does
not know how to renew credentials you obtained yourself. Use a refreshable profile
or workload provider when credentials must rotate during the application's life.

## Environment and workload credentials

The default chain checks environment credentials, shared profiles, web identity,
container credentials, and EC2 instance metadata.

The process must receive these variables; rboto does not load `.env` files.
For workload roles, configure the platform to supply its credential source.
Refreshable providers handle credential renewal through the SDK.

The tables distinguish **compiled tests** (rboto bindings exercised with fabricated
credentials, caches and local HTTP peers) from **inherited** behavior (provided by
the pinned SDK without a dedicated rboto integration case). Local tests verify
provider requests, signing and configuration; they do not provision IAM roles or
validate a real organization's login policy.

| Authentication source | Support and validation |
|---|---|
| Explicit credentials, environment and shared files | Compiled tests for signing, tokens and precedence |
| AssumeRole | Compiled tests for source profiles, explicit source credentials, external ID, caching and transport |
| `credential_process` | Compiled tests for cache reuse, expiration/refresh and bounded failures |
| Web identity / IRSA | Compiled tests for environment and profile token files and STS exchange |
| Container credentials / EKS Pod Identity | Compiled tests for a full URI, authorization token/file precedence and caching; the ECS relative-URI endpoint is inherited |
| EC2 instance role | Compiled tests for an IMDSv2 token exchange, role discovery and credential caching |
| IAM Identity Center / SSO | Compiled tests for cached legacy and `sso_session` tokens and role credentials; token renewal is SDK-owned |
| `aws login` | Compiled tests for cached credentials, refresh, cache updates and provider failures |
| Bedrock API key | Compiled test for `AWS_BEARER_TOKEN_BEDROCK`, without signing credentials |
| Roles Anywhere | Can use an external helper through `credential_process`; no dedicated Roles Anywhere test |
| Interactive MFA or SAML sign-in | No automatic interactive flow. Calling STS manually does not install a refreshable provider |
| Custom Python credential callback | No public provider callback API |

### Environment support

| Variables | Contract and validation |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | Signing credentials; compiled precedence/signing tests |
| `AWS_PROFILE`, `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE` | Profile and shared-file selection; compiled tests |
| `AWS_REGION`, `AWS_DEFAULT_REGION` | Region and fallback; compiled precedence tests |
| `AWS_ROLE_ARN`, `AWS_ROLE_SESSION_NAME`, `AWS_WEB_IDENTITY_TOKEN_FILE` | Web identity configuration; compiled tests. `AWS_ROLE_ARN` alone is not a general AssumeRole switch |
| `AWS_CONTAINER_CREDENTIALS_FULL_URI`, `AWS_CONTAINER_AUTHORIZATION_TOKEN`, `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE` | Container provider; compiled tests. A token file takes precedence over the token value |
| `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` | Inherited ECS endpoint selection; takes precedence over the full URI |
| `AWS_EC2_METADATA_DISABLED`, `AWS_EC2_METADATA_SERVICE_ENDPOINT` | Metadata enablement and endpoint; compiled tests |
| `AWS_EC2_METADATA_SERVICE_ENDPOINT_MODE` | Inherited IPv4/IPv6 endpoint selection |
| `AWS_LOGIN_CACHE_DIRECTORY` | Login cache directory; compiled cache/refresh tests |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock service bearer authentication; compiled test |
| `AWS_CA_BUNDLE` | Additional PEM trust; compiled tests for precedence, lazy loading, invalid files and hostname verification |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | Default/environment transport proxy rules; compiled tests, including with custom CA trust |
| `AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_<SERVICE>` | Global/service endpoints; compiled precedence tests with STS |
| `AWS_IGNORE_CONFIGURED_ENDPOINT_URLS` | Inherited control for ignoring environment/profile endpoints |
| `AWS_MAX_ATTEMPTS`, `AWS_RETRY_MODE` | SDK retry policy; compiled attempt-count test for an explicit override |
| `AWS_USE_FIPS_ENDPOINT`, `AWS_USE_DUALSTACK_ENDPOINT` | Inherited endpoint controls; availability depends on service and region |
| `AWS_REQUEST_CHECKSUM_CALCULATION`, `AWS_RESPONSE_CHECKSUM_VALIDATION` | Inherited checksum policies for operations that support them |
| `AWS_DISABLE_REQUEST_COMPRESSION`, `AWS_REQUEST_MIN_COMPRESSION_SIZE_BYTES` | Inherited request compression controls |
| `AWS_AUTH_SCHEME_PREFERENCE`, `AWS_SIGV4A_SIGNING_REGION_SET` | Inherited auth selection where supported by the service |
| `AWS_ACCOUNT_ID`, `AWS_ACCOUNT_ID_ENDPOINT_MODE` | Inherited account-based endpoint configuration where supported |
| `AWS_S3_DISABLE_EXPRESS_SESSION_AUTH` | Inherited S3 Express authentication setting |
| `AWS_SDK_UA_APP_ID` | Inherited application identification in the SDK user agent |

This is the supported configuration surface documented for rboto, not a promise
that every variable recognized by another SDK has the same meaning here. A setting
being recognized also does not make every service/region combination valid.
Environment changes are not a way to reconfigure an initialized client; construct a
new client. Providers still perform their own credential refresh and token-file reads.

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

`ca_bundle` adds PEM certificates to the system trust roots. When omitted,
`AWS_CA_BUNDLE` supplies the path. The explicit value wins, including when the
environment value is invalid. It applies to service and credential-provider
connections, including AssumeRole and HTTPS proxies. The file is read on a blocking
worker when the client first initializes; create a new client after rotating the
bundle. Empty environment paths and missing or invalid bundles raise
`ConfigurationError`.

These settings belong to the client and do not change the process environment.
Without `proxy_url`, the transport uses environment proxy rules, even when
`ca_bundle` or `AWS_CA_BUNDLE` is set. An explicit `proxy_url` uses only its own
`no_proxy` rules, ignoring environment proxy/bypass settings. To bypass an inherited
proxy, configure `NO_PROXY` before the client initializes. Proxy URLs and credentials
are omitted from the configuration's representation. TLS certificate and hostname
checks remain enabled.

## Local endpoints

```python
from rboto import DynamoDBClient

client = DynamoDBClient(region="us-east-1", endpoint_url="http://localhost:4566")
```

Endpoint overrides still use the credential chain. Supply test credentials in
the test process when the local service expects signed requests. Remote HTTPS
endpoints require trusted certificates; there is no `verify=False` option.
