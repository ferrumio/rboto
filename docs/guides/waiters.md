# Waiters

Creating or updating a resource can finish before that resource is ready to use.
Waiters poll a read operation until a modeled condition succeeds or fails:

```python
--8<-- "examples/docs/waiters.py"
```

Each method under `client.waiters` has explicit, typed keyword arguments from
its polling operation. Your editor completes the arguments and returned Output.
`client.waiters.names` lists the available waiter names. Clients without validated
definitions do not expose a `waiters` property.

See the [generated catalog](../reference/waiters.md) for every waiter, polling
operation, default interval, and full signature. For example:

- `DynamoDBClient.waiters.table_exists` polls `describe_table`.
- `LambdaClient.waiters.function_active` polls `get_function_configuration`.
- `LambdaClient.waiters.function_active_v2` polls `get_function`.
- `S3Client.waiters.object_exists` polls `head_object`.

Use the waiters shown on your installed client's namespace; the website can
describe a newer source version.

## Results and failures

A waiter that succeeds on an output returns that operation's regular Output.
A disappearance waiter such as `table_not_exists` returns `None` when the modeled
not-found error confirms success. A definition permitting both forms of success
has an `Output | None` return annotation.

Conditions are checked in model order. An unmatched successful response, a
missing field, or an unknown status continues polling. An error continues
polling only if a retry acceptor explicitly matches it. Other service,
credential, transport, and interceptor exceptions propagate unchanged.

`WaiterFailedError` means a terminal failure acceptor matched.
`WaiterTimeoutError` means the maximum polls or overall deadline was reached;
its `reason` is `"max_attempts"` or `"deadline"`. Both inherit from `WaiterError`
and `RbotoError`. Import waiter errors from `rboto.waiters`.

These errors expose `waiter_name`, `attempts`, and `last_attempt`. The latter
contains the last completed poll's number, elapsed time, matched acceptor index,
and any modeled error code and request ID. It is `None` when no poll completed.
It does not convert or retain an Output as a diagnostic payload.

## Polling controls

`WaiterConfig` is independent of `ClientConfig`:

| Setting | Default | Meaning |
|---|---|---|
| `delay` | Model's `minDelay` | Fixed seconds between completed polls. `0` still yields to the event loop. |
| `max_attempts` | `40` | Maximum operation calls, including the first poll. Must be positive. |
| `timeout` | `None` | Overall elapsed seconds, including requests, native retries, middleware and sleeps. |

The first poll starts immediately. A matched success or failure ends immediately,
without a final sleep. `delay` is a fixed interval; it does not grow toward the
model's `maxDelay`. There is no additional retry policy around an operation:
native retries follow `ClientConfig`, and all native attempts in one operation
count as one waiter poll.

Set an overall `timeout` when you need a time budget. Without one, the maximum
poll count bounds calls, but not the duration of an individual call. An operation's
own timeout remains a separate failure and is not reclassified as a waiter deadline.

## Async execution and cancellation

Requests use the public async client operation, preserving authentication,
interceptors, logging, response types, and native request retries. Wait intervals
use cooperative async sleeps. No polling thread, background task, prefetch, or
shared polling state is created. Concurrent waiters may use the same client.

The deadline covers both a pending request and a sleep. Cancelling the calling
task propagates `asyncio.CancelledError` and stops further polls. Cancelling a
request does not undo anything the service already received.

Deadlines are cooperative: synchronous Python work or an interceptor that
suppresses cancellation can delay their delivery. Condition checks read the
necessary response attributes on the event-loop thread; they do not call
`to_dict()`. Ordinary native conversions and user middleware retain their
existing [async execution boundaries](async-only.md).
