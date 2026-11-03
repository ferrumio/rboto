# ADR-0026: Generate typed async waiters from validated metadata

- Status: Accepted
- Date: 2026-11-03
- Deciders: rboto maintainers

## Context

Resource creation and update responses can precede readiness. Handwritten polling
duplicates state checks, deadlines, and exception handling. The paginator namespace
already separates higher-level helpers from the one-to-one native operation API.

## Decision

Generate `client.waiters.<name>` from Smithy `smithy.waiters#waitable` traits and
optional `[waiters.Name]` descriptor supplements with the same matcher schema plus
`operation`. A supplement replaces a same-named model definition. Validate operation,
member, enum, error, comparator, name, and stream ownership contracts at generation
time. Unsupported definitions fail generation rather than disappearing silently.

JMESPath parsing is a codegen-only dependency. Compile a model-checked subset
(fields, indices, list projections/filters, scalar literals/comparisons, boolean
expressions, and `length`) into a small expression representation. The shared
runtime evaluates it against native attributes or mapping fields without `to_dict`.
Do not evaluate unrestricted expressions or copy whole responses to dictionaries.
HTTP status and input/output matchers are not supported: generation rejects them.
Error matchers must resolve to errors modeled by the polling operation.

Waiter methods expose the polling operation's explicit keyword parameters and a
separate `waiter_config`. A collision with that parameter fails generation. No
waiter methods appear on the native operation class. Services without definitions
have no waiter namespace; services with definitions expose typed methods and `names`.

The private shared poller calls the public operation on every poll. It retains
middleware, configuration and native request retries. The first poll is immediate;
subsequent polls use a fixed delay defaulting to model `minDelay`, not an exponential
schedule based on `maxDelay`. Default maximum polls is 40; an optional monotonic
deadline includes requests, retries, middleware and sleeps. Waits are cooperative.
No extra request retry layer, worker, prefetch or background polling task is added.

Model acceptor order determines success/retry/failure. Missing output fields do
not satisfy comparisons; unmatched successful responses continue. Unmatched service
errors and non-service exceptions propagate. A successful output acceptor returns
the exact native Output; an error success acceptor returns `None`. Generated return
annotations reflect the permitted forms of success.

Terminal acceptors raise `WaiterFailedError`. Exhaustion raises `WaiterTimeoutError`
with a reason distinguishing poll count from deadline. Both retain bounded
last-completed-attempt metadata, not materialized output diagnostics. External
cancellation propagates. Stop polling immediately after success, failure,
exhaustion or cancellation; release the previous response before sleeping.

## Consequences

- New services need metadata, not service-name branches in the polling runtime.
- No new dependency or compiled code is added to service wheels.
- Model updates fail visibly when a new expression needs compiler support.
- Output matching and Python middleware still execute on the event-loop thread.
- Documentation catalogs and signatures are generated only during website builds.
- Generic changes require regeneration, strict typing, budget/cancellation tests,
  and compiled protocol tests including native retries within a single poll.
