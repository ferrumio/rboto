# Closing clients and streams

Clients and response streams support `async with` and `await ...aclose()`.
Entering a client context does not load credentials or send requests.

This example reads the first chunk of an object and closes the remaining body,
including when reading raises an exception:

```python
--8<-- "examples/docs/client_lifecycle.py"
```

## Clients

Reuse a client for related operations. When your application no longer needs it,
await `client.aclose()`, or leave its async context.

Closing is terminal and safe to repeat, including concurrently. New operations
and re-entering a closed client raise `rboto.exceptions.ClientClosedError`.
Native operations still in progress, including credential/configuration loading,
are interrupted with the same exception. Closure waits for their native futures
and resource owners to be released. It cannot undo a request already accepted by
the service.

Application middleware already running continues on its caller's task. Closing
the client does not cancel or wait for that application code. If middleware tries
to dispatch afterward, the native call raises `ClientClosedError`. Paginators and
waiters encounter closure on their next operation. Synchronous conversion work
cannot be interrupted in the middle.

The close signal remains in effect if the task awaiting `aclose()` is cancelled.
Call `aclose()` again to wait for cleanup to finish. An application can bound its
wait with `asyncio.timeout()`.

Closing one client does not close other clients or shut down the service's shared
Tokio runtime. Closing a client that was never used does not start that runtime.
Regular output attributes remain readable after closure.

## Response streams

A returned body or event receiver belongs to its response. **It remains usable
after the originating client closes.** Close it separately when finished,
especially when consuming only part of the response:

```python
async with response.body as body:
    async for chunk in body:
        ...
```

Use the same pattern with typed event receivers, or call `await stream.aclose()`.
Closing does not consume the rest of the stream.

| Situation | Result |
|---|---|
| Close an unread or partially consumed stream | Release its native body or receiver |
| Close during `read()` | The pending read raises `rboto.exceptions.StreamClosedError` |
| Close during `anext()` | Pending iteration ends with `StopAsyncIteration` |
| Read after closure | Return `b""` |
| Iterate after closure | No further items |
| Enter a closed stream context | Raise `StreamClosedError` |
| Close more than once | Wait for the same cleanup; no error |

Keep one consumer per stream. Cancelling a collecting `read()` discards the
remaining body. Cancelling an iterator advance leaves its native receiver in the
wrapper for subsequent iteration; explicit closure prevents that restoration.
A stream cannot be cloned or rewound.

Closing a bidirectional response releases the native receiver. An application
owns its input iterator and must close it separately if it needs explicit cleanup.
When native consumption stops, rboto schedules cancellation of its pending Python
input poll on the originating loop; an async generator may then run its `finally`.
For normal chunk and event consumption, see [Streaming](streaming.md).
