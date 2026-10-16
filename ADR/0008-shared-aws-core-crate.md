# ADR-0008: Share AWS configuration, credentials, and error mapping through an external Rust crate

- Status: Proposed
- Date: 2026-10-16
- Deciders: rboto maintainers

## Context

Every rboto service adapter must load AWS configuration, resolve credentials, and
normalize SDK failures before it can expose a single operation. Today this logic lives in
the `lib.rs.j2` template and is copied into every generated service crate. It only
supports `region`, `profile`, and `endpoint_url`. Static credentials, session tokens,
AssumeRole, timeouts, and retry configuration are not available. Non-service SDK failures
(`DispatchFailure`, `TimeoutError`, `ConstructionFailure`, `ResponseError`) are not
mapped: a missing credential or an unreachable endpoint surfaces as the service base
exception with error code `UnknownError` and a Rust debug string as the message. The
facade already defines `CredentialsError` and `ConfigurationError`, but no Rust code
raises them, and `request_id` is always `None`.

The same organization maintains pydynox, a stable PyO3 project that solved exactly this
problem in its `client_internal` module (see pydynox ADR 016): a shared `AwsConfig`
struct, a credential provider chain (static, AssumeRole with external ID, profile with
SSO, default chain), region resolution, timeout and retry configuration, and typed
mapping of outer `SdkError` variants. That code has been exercised in production.

Both projects are PyO3 extensions linking the AWS SDK for Rust. Rust has no stable ABI,
so sharing a compiled library between extension modules is not possible. Sharing is only
possible at the source level: a library crate compiled statically into each `cdylib`.

The decision is therefore where this critical logic should live and be tested: copied
into rboto, kept in `crates/rboto-core`, or extracted into a crate that both projects
depend on.

## Decision

Extract the shared AWS client foundation into an independent Rust library crate,
maintained in its own repository and published to crates.io. Both rboto and pydynox
depend on it. The crate name is neutral with respect to either project.

### Scope of the shared crate

The crate contains only logic that is identical for every consumer:

- `AwsConfig`: region, static access key and secret, session token, profile, AssumeRole
  (`role_arn`, `role_session_name`, `external_id`), connect and read timeouts, and
  maximum retry attempts. A plain struct with no PyO3 dependency.
- Credential provider selection with a fixed priority: static credentials, AssumeRole,
  profile, then the default chain. Profiles cover SSO. The default chain covers
  environment variables, instance and container metadata, EKS IRSA, and web identity.
- `build_sdk_config(&AwsConfig) -> Result<SdkConfig, CoreError>`, which applies region,
  credentials, timeouts, and retries through `aws-config`.
- A neutral `CoreError` enum covering missing region, credential resolution failure,
  connection failure, timeout, construction failure, and invalid response, with the
  service name as context.
- Generic extraction of `code`, `message`, and `request_id` from any `SdkError` through
  `ProvideErrorMetadata` and `RequestId`, and mapping of the outer `SdkError` variants
  to `CoreError`.
- Optionally, behind a `pyo3` cargo feature: extraction of `AwsConfig` from a Python
  mapping, and the async `ByteStream` wrapper currently duplicated in every rboto
  service crate.

Dependencies are limited to `aws-config`, `aws-credential-types`, `aws-types`,
`aws-smithy-runtime-api`, `aws-smithy-types`, and `tokio`. The crate must not depend on
any `aws-sdk-*` service crate.

### What stays in each consumer

- Python exception classes and the mapping from `CoreError` to them. rboto maps to
  `rboto.exceptions.CredentialsError`, `ConfigurationError`, and `ClientError`; pydynox
  maps to its own `CredentialsException` and `ConnectionException`. The shared crate
  never creates Python exception types.
- Service client construction. Consumers call
  `aws_sdk_<service>::config::Builder::from(&sdk_config)` and apply service-specific
  settings such as `endpoint_url` and `force_path_style`.
- The Tokio runtime. rboto uses the `pyo3-async-runtimes` runtime; pydynox owns an
  explicit runtime. The shared crate does not create or require a runtime.
- Project-specific behavior: rboto generated outputs and exceptions; pydynox locks, rate
  limiting, KMS envelope encryption, and S3 attributes.

### Behavioral rules fixed by this ADR

- The shared crate does not fall back to a default region. A missing region returns
  `CoreError::NoRegion`. A consumer may apply its own fallback for compatibility, but
  rboto raises `ConfigurationError`.
- The shared crate does not read `AWS_ENDPOINT_URL` or `AWS_ENDPOINT_URL_<SERVICE>`
  itself. `aws-config` already honors these variables and the matching profile keys.
- Credentials are resolved lazily on the first operation, never at client construction.

### Integration in rboto

- `crates/rboto-core` becomes a thin rboto-specific layer: `raise_service_error`, the
  `CoreError` to rboto exception mapping, and nothing else. It depends on the shared
  crate.
- `lib.rs.j2` shrinks to: build `AwsConfig` from the Python client arguments, call the
  shared `build_sdk_config`, apply service-specific builder settings, and delegate error
  mapping to `rboto-core`.
- `rboto.Session` and every generated client constructor expose the full `AwsConfig`
  surface with keyword-only parameters.
- The crate version is pinned in `Cargo.lock` and updated by Dependabot like any other
  Rust dependency. `aws-config` and `pyo3` versions must be compatible across the shared
  crate and the workspace, so rboto upgrades to the versions pydynox already uses.

### Order of work

1. Create the crate from pydynox `client_internal`, porting its unit tests and adding a
   LocalStack integration test for the credential chain. Publish `0.1.0`.
2. Make rboto the first consumer and regenerate all services.
3. Migrate pydynox in a minor release once the crate has a stable `1.0` API.

## Consequences

- Credential, region, timeout, and retry behavior is implemented and tested once and is
  identical across ferrumio PyO3 projects.
- rboto gains static credentials, AssumeRole, SSO profiles, timeouts, retries, typed
  non-service errors, and `request_id` without writing new credential code.
- A third repository with its own CI, release cycle, and crates.io publication is
  required. A fix in the shared crate requires a release and a dependency bump in every
  consumer.
- `aws-config` and `pyo3` versions move in lockstep across consumers. Divergence blocks
  upgrades until all consumers align.
- Each extension module still carries its own compiled copy of the crate, its own Tokio
  runtime, and its own credential cache. Sharing resolved credentials between rboto
  service wheels remains out of scope for this ADR.
- The shared crate becomes a single point of failure for both projects and must be held
  to the same quality bar as a release artifact: unit tests, LocalStack integration,
  Clippy with denied warnings, and locked dependencies.
- Path dependencies are not allowed because they break source distributions. Git
  dependencies are allowed only temporarily before the first crates.io release.

## Alternatives considered

- Copy pydynox `client_internal` into `crates/rboto-core`: rejected because the two
  copies would drift, and a bug fixed in one project would remain in the other.
- Keep the logic in `lib.rs.j2`: rejected because it duplicates critical code into every
  generated crate and cannot be unit tested independently of a service.
- Publish a shared native Python wheel that services link against: rejected because
  Rust has no stable ABI and PyO3 extension modules cannot share Rust types across
  shared-library boundaries.
- Share credentials through a Python-level provider callback between wheels: deferred.
  It would remove duplicate credential resolution across rboto service wheels but adds
  a Python round trip to the signing path and is independent of where the Rust code
  lives.
- Monorepo or git subtree for the shared code: rejected because it couples the release
  cycles of unrelated projects and does not produce a reusable artifact for third
  parties.

## References

- `crates/rboto-core/src/lib.rs`
- `codegen/src/rboto_codegen/templates/lib.rs.j2`
- `packages/rboto/src/rboto/exceptions.py`
- `packages/rboto/src/rboto/session.py`
- pydynox `src/client_internal/{config,auth,builder}.rs` and `src/errors.rs`
- pydynox ADR 016: Unified AWS Client Configuration
- ADR-0001, ADR-0005
