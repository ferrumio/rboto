# Interceptors

An interceptor is async middleware around an operation. It receives an
`OperationRequest` and `call_next`, which runs the rest of the chain. It returns
an `OperationResponse`. The client unwraps that response and returns the usual
typed output to your application.

Use middleware to change parameters, enforce an application policy, time an
operation, recover from a failure, or serve a cached response.

For standard operation logs, register the built-in
[`LoggingInterceptor`](logging.md).

```python
--8<-- "examples/docs/interceptors.py"
```

## How the chain runs

For `interceptors=[first, second]`, execution proceeds as follows:

```text
first: before
  second: before
    native operation, including its retries
  second: after
first: after
```

`await call_next(request)` forwards the operation. Returning an
`OperationResponse` without calling it skips the remaining chain and the native
request. Raising an exception also prevents forwarding if `call_next` has not
run yet. Exceptions propagate through the chain to the caller unless a
middleware explicitly catches them.

Each continuation can be awaited once, on the middleware's task, and only while
that middleware is running. This prevents accidental duplicate sends and
continuations outliving the operation. For a deadline, use `asyncio.timeout()`
around `await call_next(request)`. Native retries remain under the client's retry
configuration; calling `call_next` twice is not a retry API.

## Change parameters

`request.parameters` contains the same snake_case names used by the client
method. For Python keyword escapes, use the underlying name, such as `from`
instead of `from_`. Values include the original typed mappings and stream
objects; the chain does not deep-copy them.

```python
--8<-- "examples/docs/interceptor_parameters.py"
```

You can also forward a replacement mapping:

```python
updated = request.with_parameters({**request.parameters, "max_keys": 100})
return await call_next(updated)
```

Unknown top-level parameters and missing required parameters are rejected before
native dispatch. Values then pass through the usual native conversion and
validation. Middleware shared across services must check the service and
operation before changing operation-specific parameters.

## Return a cached response

The following example caches the immutable output of `ListBuckets` without
parameters for five seconds. Create a separate cache instance for each client
configuration and credential scope.

```python
--8<-- "examples/docs/interceptor_cache.py"
```

Register it with `S3Client(interceptors=[ListBucketsCache()])`. A cache hit returns
the saved native output without sending another request. In concurrent calls,
this small example may make more than one request while the cache is empty.

A replacement must satisfy the operation's output contract. Regular native
outputs remain immutable: return another output of the same generated class
rather than a dictionary with similar fields. Event-stream operations retain
their documented mapping outputs. Do not cache consumable streams.

After forwarding, you can assign a replacement to `response.output` and return
that response, or return a new `OperationResponse`. The same output contract
applies in both cases.

## Handle failures

Use ordinary `try`/`except` around `await call_next(request)`. Modeled exceptions
retain their classes. A middleware can re-raise, translate an exception, or
return a valid fallback output. Errors in middleware are not swallowed.

Use `try`/`finally` for cleanup and instrumentation. Cancellation unwinds the
chain normally, allowing each middleware's `finally` block to run. Avoid blocking
I/O and unbounded cleanup waits on the event loop.

## Configuration and context

A client inherits its Session's middleware unless an explicit sequence replaces
it. An empty sequence disables inheritance:

```python
client = S3Client(session=session)                         # inherit
client = S3Client(session=session, interceptors=[timing])  # replace
client = S3Client(session=session, interceptors=())        # disable
```

The sequence is copied at construction. Without middleware, operations take the
direct native path. Compatibility factories accept the same setting.

`request.context` is immutable. It provides `service`, `operation`,
`operation_id`, and `correlation_id`. Each invocation has a unique ID.
`operation_context(correlation_id=...)` attaches an application ID to calls in
that block, including child tasks created there. Nested blocks restore the
previous ID on exit.

## Native metadata

Inspect `request.metadata` after `call_next` returns or raises. It is an immutable
snapshot of native dispatch, separate from any later response replacement:

| Field | Meaning |
| --- | --- |
| `dispatched` | Whether the native adapter was called; false for cache hits and blocked calls |
| `region` | Explicit region initially, then resolved region when available |
| `duration` | Native call elapsed seconds, including initialization and retries |
| `attempt_count` / `retry_count` | Native attempts and retries |
| `status_code` / `request_id` | Last attempt's status and request ID when available |
| `error_code` / `error_type` | Native failure's modeled code and Python exception class name |
| `attempts` | Up to 32 completed attempts, with number, duration, status, and request ID |
| `attempts_truncated` | Whether the attempt list omitted additional records |

Before dispatch, `attempt_count` is zero. After dispatch, `None` means unavailable,
for example when cancellation prevents metadata delivery. Attempt duration
excludes backoff before the next attempt. Attempt metadata arrives when native
dispatch finishes, rather than as live retry notifications.

A middleware that recovers from an error still sees that native error in the
metadata. To time the entire chain, including middleware and cache hits, use an
outer middleware as in the first example.

## Streaming outputs

`call_next` may return while a byte stream or event stream is still unread.
Timing the operation does not measure subsequent consumption. Later stream
errors are handled by the code reading the stream.

The chain does not read, clone, or convert responses with `to_dict()`. Middleware
has access to the actual output, so consuming its stream would also consume it
for the caller. Preserve ownership when replacing outputs or handling streams.
