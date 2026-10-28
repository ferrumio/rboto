# ADR-0014: Expose asynchronous operation middleware

- Status: Accepted
- Date: 2026-10-28
- Deciders: rboto maintainers

## Context

Applications need to enforce policies, change operation parameters, inspect
responses, recover from failures, and return cached outputs. Observability also
needs a place to measure the operation and inspect native retry metadata.

## Decision

`Interceptor` is an async callable taking `OperationRequest` and `Next`, and
returning `OperationResponse`. It controls whether the rest of the chain runs.
The first configured middleware is the outermost: before-work runs in order,
and after-work unwinds in reverse order.

Requests expose immutable operation identity and a mutable parameter dictionary.
`with_parameters()` replaces the mapping while preserving invocation identity and
shared native metadata. Generated methods retain their strictly typed public
signatures. Generic middleware uses `dict[str, object]` because it can see any
service operation. Unknown and missing required top-level parameters are rejected
before native dispatch; native converters retain value validation.

Responses wrap the actual output, which is unwrapped before the public method
returns. Middleware can return a response without calling the continuation or
replace a downstream response. Replacements must honor the generated output
contract. Native output classes remain immutable and replacements are checked
against the expected runtime class; mapping outputs retain their existing schema
contract. The chain never materializes native responses automatically.

Each continuation is scoped to the middleware invocation, may be called once,
and must be awaited on the middleware's task. Retaining it for later or using it
from a detached task raises an error. Use `asyncio.timeout` for deadlines. This
keeps request/stream ownership and operation side effects explicit. Native retry
configuration remains the retry mechanism.

Middleware exceptions propagate; there is no automatic suppression or logging.
Outer middleware may catch and translate failures or return fallback responses.
Cancellation unwinds `finally` blocks normally. No background delivery task or
independent observer lifecycle is added.

Clients copy middleware sequences at construction. Omitted client settings inherit
from Session; explicit sequences replace inheritance, including an empty sequence.
ContextVar-based correlation is snapshotted per invocation. No shared Rust objects
across service extension modules or global middleware registration is required.

The existing per-operation native collector supplies immutable dispatch metadata
through `request.metadata`, including failures. It counts all attempts and retains
at most 32 detailed records without reading request/response bodies. Metadata is
published once when native dispatch completes; missing data after cancellation
remains unknown. A late native completion cannot mutate the delivered snapshot.
No-dispatch results report zero native attempts. Recovered errors retain their
native failure metadata, separate from the middleware's final outcome.

Empty middleware configuration keeps direct dispatch. Generic Python generation
changes apply to all services. Native methods retain their same-named alignment
with public methods and their optional private metadata sink.

## Consequences

- Policies can block before I/O, and applications can intentionally recover from
  native errors or return cached outputs.
- Logging and tracing use middleware with `try`/`finally`.
- Middleware is trusted application code and can inspect inputs and outputs.
  It is responsible for data handling and any response stream it consumes.
- Regular output typing, modeled errors, async execution, and stream ownership
  remain unchanged unless middleware intentionally alters the outcome.
