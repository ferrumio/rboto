# ADR-0019: Generate typed paginators from validated metadata

- Status: Accepted
- Date: 2026-10-29
- Deciders: rboto maintainers

## Context

Separating input and response contracts makes manually forwarding structured
continuation tokens cumbersome. A public adapter namespace adds concepts to
every service even when the application only needs to iterate over pages.

## Decision

Remove the public `inputs` adapter API. Keep request contracts and response
contracts distinct as defined by ADR-0018.

Generate `client.paginators.operation(...)` with the operation's explicit,
keyword-only arguments and `AsyncIterator[Response]` return type. Each page
calls the public async operation. Direct calls continue to return one page.
The separate namespace preserves Python/PyO3 operation method alignment.

Read operation pagination traits and service defaults from Smithy. Supplement
missing or incomplete traits with operation tables in service TOML. Validate
operation names, field paths, cursor type compatibility, integer page-size
fields, boolean completion flags, and stream ownership during generation.
Support multiple cursors, nested paths, integer list indices, and ordered
fallback paths declaratively. Do not branch on service or operation names.

Use a private shared Python iterator for continuation state. Only cursor state
is copied; response pages retain their native representation. Snapshot state
before yielding so consumer mutations cannot change the next request. Stop
when the service signals completion, all tokens are absent, or the next cursor
equals the previous cursor. Empty result collections alone do not stop it.
Do not prefetch or impose a second retry policy.

The internal callable boundary accepts the generated argument dictionary;
public paginator signatures retain strict parameter and response types.
No public cursor adapter, untyped `**kwargs` API, flattened-items API,
opaque resume-token format, or total-item limiter is introduced.

## Consequences

- Editors discover supported operations and their typed arguments directly.
- Logging, interceptors, authentication, cancellation, and modeled failures
  follow the existing operation path for every page.
- Structured continuation state is handled internally without widening inputs.
- Pagination metadata that cannot resolve against the pinned model is rejected.
  `GetSdkTypes`, for example, has no modeled output cursor in the current pin;
  it is not exposed as a paginator.
- Reusing arbitrary response values as new requests remains a separate typing
  concern; pagination does not make unknown union variants serializable.
- Generic changes regenerate every service and require typing, iterator,
  metadata validation, and representative compiled protocol tests.
