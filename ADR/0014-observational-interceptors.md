# ADR-0014: Share typed operation lifecycle events

- Status: Accepted
- Date: 2026-10-28
- Deciders: rboto maintainers

## Context

Logging, tracing, and user callbacks need a consistent account of an operation,
including native retries. Independent wrappers would duplicate behavior and make
cancellation, response ownership, and concurrent context difficult to maintain.

## Decision

Add observational `Interceptor` callbacks accepting an immutable `OperationEvent`.
Callables may return `None` or an awaitable. All callbacks run sequentially, in
registration order, on the caller's Python task; their return values do not
replace operation inputs or outputs.

Clients copy their interceptor sequence into a tuple at construction. An omitted
client setting inherits the Session's tuple; any explicit sequence replaces it,
including an empty sequence. Session configuration is immutable. No global
registration or Rust object shared between extension libraries is required.

The generated public methods keep their signatures and same-named PyO3 dispatch.
An empty tuple takes the direct native path. Configured calls use a common Python
helper and pass a private metadata dictionary to the same native method.

Each invocation receives a random identity and a snapshot of the correlation ID
from a ContextVar. `operation_context()` supplies lexical, nested context and
restores it on exit. Public context and events contain no inputs, outputs, or
exception objects.

Each observed call emits a start event and one terminal event: success, failure,
or cancellation. Registration order also applies to terminal events. A native
result settles the outcome before terminal delivery: cancellation while a
terminal callback runs does not create a second terminal event. The interrupted
callback is not restarted; remaining callbacks receive the settled event before
cancellation propagates. Cancellation during start delivery skips remaining
start callbacks but still delivers a terminal cancellation event.

Ordinary callback exceptions are reported to the event loop's exception handler
and do not replace the operation result or stop subsequent callbacks. A callback
raising CancelledError without a pending task cancellation is treated the same
way. Actual task cancellation propagates. Callbacks can delay the caller, so they
must avoid blocking work and unbounded waits. Their own programming exceptions
may appear in the application's event-loop diagnostics.

A per-operation native SDK interceptor counts attempts and records monotonic
durations, status, and request IDs from response headers. It never calls Python
from SDK hooks. At most 32 completed attempt records are retained; total counts
remain exact and truncation is explicit. Request bodies, response bodies, and
unrestricted headers are never inspected.

The native adapter publishes metadata once after send completes, including
resolved region. Python snapshots this data into frozen events. If cancellation
or pre-dispatch validation prevents publication, unavailable values stay None.
Late native completion cannot mutate delivered events or emit another event.
Retries remain entirely under the native SDK's control.

Operation duration is elapsed seconds from before start callbacks until native
completion/failure/cancellation, excluding terminal callbacks. It includes start
callback time, initialization, and retry backoff. Attempt duration is measured
inside native attempt hooks and excludes time waiting between attempts.

Success means the operation returned its result. A returned byte stream or event
stream is still owned by the caller. Consumption, later stream errors, and stream
cancellation produce no additional operation events. No observer materializes
native outputs, invokes `to_dict()`, or reads a stream.

## Consequences

- Logging and OpenTelemetry can consume one shared lifecycle.
- Callback failures remain diagnosable without discarding modeled errors.
- Optional instrumentation has a measurable cost; disabled and enabled paths
  require separate benchmarks.
- Arbitrary request mutation, response replacement, short-circuiting, live Python
  callbacks for each native attempt, and stream-lifetime hooks remain separate
  API decisions.
