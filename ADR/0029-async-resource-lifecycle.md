# ADR-0029: Explicit async client and stream lifetimes

- Status: Accepted
- Date: 2026-11-04

## Decision

Clients expose `aclose()` and async context management. Entering a context checks
that the client is open without loading credentials or initializing its SDK.
Closing is terminal and idempotent. New operations, including middleware-only
calls, fail with `rboto.exceptions.ClientClosedError`.

Closure marks the native client closed, drops its stored SDK/configuration owner,
and signals native operations, including first-use configuration loading, to stop.
`aclose()` waits until those operation futures and their resource owners have
been dropped. Interrupted native calls raise `ClientClosedError`. This does not
undo requests already accepted by a remote service. Synchronous conversions run
to completion; closure is not a preemptive interruption of Python code.

Application middleware already executing remains on its caller's task. Client
closure does not cancel arbitrary application callbacks or wait for them. A
continuation attempting native dispatch after closure fails. Paginators and
waiters encounter closure on their next operation.

Returned streams own their bodies/receivers independently of the client.
Closing a client leaves these streams usable, and ordinary immutable outputs
remain readable. Applications close streams explicitly or use their async context
managers. Byte and event streams expose `aclose()`. Closure drops an unread
receiver, interrupts a pending read/receive, and waits for its native owner to
drop. Interrupted byte collection raises `StreamClosedError`; interrupted
iteration ends with `StopAsyncIteration`. After closure, `read()` returns empty
bytes and iteration is exhausted. Entering a closed stream raises
`StreamClosedError`.

Stream ownership remains single-consumer. Cancelling an iterator advance returns
the native receiver to its wrapper, preserving the existing iteration behavior.
Explicit closure prevents this restoration. Concurrent iterator advances remain
serialized. A collecting read takes the remaining body once, so competing reads
still observe exhaustion and cancelling collection discards the remaining body.
Closing a returned bidirectional receiver drops the SDK receiver. Dropping a
native input poll schedules cancellation of that individual Python poll on its
originating loop. Sources remain application-owned; rboto never calls their
`aclose()`. An async generator's own cancellation may execute its `finally`.

The close signal and removal of the stored resource happen before returning the
native close awaitable. Cancelling that awaitable does not reopen the resource or
withdraw the signal. Another `aclose()` can await completion. Native cleanup does
not require the caller's event loop to remain alive. Cleanup uses bounded locks
and asynchronous notifications; resource destruction releases the GIL.

All lifecycle state stays inside its extension. Closure never shuts down the
extension-global Tokio runtime. Closing an unused client does not start that
runtime. No new runtime, pool, cross-wheel pointer, background cleanup task, or
automatic retry layer is introduced.

## Validation

Exercise all generated clients through compiled lifecycle/operation contracts.
Controlled peers cover initialization races, pending requests, unread and partial
byte/event bodies, cancellation, concurrent closure, and connection release.
Other clients and independently owned streams must continue working. Retain
strict typing, modeled errors, and operation-to-native alignment.
