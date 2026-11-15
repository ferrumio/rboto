# ADR-0036: Support Trio through a shared native bridge

- Status: Accepted
- Date: 2026-11-15
- Extends: ADR-0004, ADR-0014, ADR-0024, ADR-0029, ADR-0030

## Context

The existing pyo3-async-runtimes bridge creates asyncio futures. AnyIO works
with its asyncio backend, but Trio calls fail before dispatch because no
asyncio loop exists. Pure-Python waiters, middleware task checks, and cancellation
classification also depend on asyncio.

## Decision

Keep the extension-local Tokio pools and their existing worker policy. Detect
the scheduler of each calling task, including Trio guest tasks with an asyncio
host. Preserve the existing asyncio future conversion. Add a direct Trio bridge
inside rboto-core, with Python scheduler helpers in private rboto modules.

Trio remains optional through `rboto[trio]`. Default imports and asyncio calls
do not import Trio. AnyIO is a development/test dependency; applications may
use either of its backends without a separate rboto API.

A Trio native awaitable owns one completion and an abort handle. Native result
conversion acquires Python on a Tokio blocking worker. TrioToken schedules
delivery on the originating run; a Trio event wakes the waiting task. Cancellation
aborts the native task and discards late results. Completion after the Trio run
ends releases the result without restarting a scheduler.

Python input iteration uses one owned Trio system task per pending iterator
advance, with the caller's captured context. The native pending-poll guard cancels
that task when its Rust future is dropped. There is no eager producer loop or
additional buffered chunk. Catch producer exceptions, including cancellation,
and return them through a one-shot channel; never allow them to escape into
Trio's system nursery. The application retains ownership of its iterator.

Use scheduler-aware helpers for clocks, deadlines, sleep, task identity, and
cancellation classification. Preserve waiter-specific timeout errors and the
original external cancellation exception. Logging and OpenTelemetry classify
both cancellation classes as cancellation rather than operation failure.

Close signals retain their existing synchronous terminal transition. Waiting
for cleanup uses the current scheduler, including for an unused client, without
starting a Tokio pool. Streams remain independent of client lifetime.

Public methods, same-named PyO3 methods, request types, outputs, and modeled
exceptions do not change. No service-specific runtime branches are introduced.

## Validation

Run the same compiled contracts on asyncio and Trio, including AnyIO task
groups/cancellation scopes, ordinary and bidirectional streams, Python producers,
timeouts, pagination, waiters, middleware context, logging and OpenTelemetry.
Check connection release after cancellation and closure.

Exercise result/cancellation races, cancellation before an input poll starts,
duplicate and late completion, and delivery after Trio shutdown. Verify a shared
client across asyncio/Trio threads and Trio guest runs. Each wheel runs the
selected client's lifecycle contract on both backends.
If an input poll is queued after Trio closes its system nursery, return the
shutdown error to Rust; the callback must not fail Trio's run.

Keep asyncio regression tests, strict typing, source regeneration, and Rust
quality checks. Local controlled peers prove bridge behavior, not every service
operation or every release platform.
