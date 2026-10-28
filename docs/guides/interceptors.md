# Interceptors

Use an interceptor to observe calls without wrapping each operation. A callback
receives an `OperationEvent` before the call and another when it succeeds, fails,
or is cancelled.

```python
--8<-- "examples/docs/interceptors.py"
```

Callbacks may be ordinary functions or `async def` functions. rboto calls them in
registration order on the task making the request, awaiting async callbacks.
Callbacks receive metadata; they cannot change parameters or replace responses.
Keep them short: blocking I/O in a callback blocks your event loop, and awaiting a
slow callback delays the operation.

## Configure a client or Session

Pass `interceptors=[report]` directly to any client. A client constructed with a
Session inherits its interceptors unless you provide an explicit sequence:

```python
client = S3Client(session=session)                       # inherit
client = S3Client(session=session, interceptors=[report])  # replace
client = S3Client(session=session, interceptors=())       # disable
```

The client copies the sequence at construction. Changing the original list later
does not change existing clients. Compatibility factories accept the same option.
Without interceptors, operations take the direct native path.

## Read the event

Events and their nested context and attempt records are immutable.

| Field | Meaning |
| --- | --- |
| `context.operation_id` | Unique identity for this invocation, shared by its two events |
| `context.service` / `context.operation` | Service ID and modeled operation name, such as `s3` / `GetObject` |
| `context.correlation_id` | ID supplied through `operation_context()` |
| `phase` | `start`, `success`, `failure`, or `cancelled` |
| `region` | Explicit region at start; resolved region at completion when available |
| `duration` | Elapsed seconds, available on terminal events |
| `attempt_count` / `retry_count` | Total native attempts and retries |
| `status_code` / `request_id` | Last attempt's HTTP status and request ID, when available |
| `error_code` / `error_type` | Modeled error code and Python exception class name |
| `attempts` | Completed native attempts with number, duration, status, and request ID |
| `attempts_truncated` | Whether the attempt list omitted records after its first 32 entries |

`None` means unavailable. For example, cancelling a call before the native result
returns may leave its attempt count unknown. The total count remains exact when
available even if the attempt list was truncated.

Operation duration includes client initialization, start callbacks, retries, and
time waiting between attempts. It excludes terminal callbacks. Each attempt's
duration excludes the wait before the next attempt. Attempt records arrive with
the terminal event, not as live retry notifications.

Events contain no request parameters, response payloads, exception messages, or
credentials. Request IDs depend on the response metadata available to the client.

## Correlate concurrent calls

`operation_context(correlation_id=...)` attaches an ID to operations within the
block, including child tasks created there. Nested blocks restore the previous
ID on exit. Different tasks can use different IDs with the same client.

Callbacks still need to protect any mutable application state they share.

## Failures and cancellation

An ordinary callback exception is sent to your event loop's exception handler.
Other callbacks continue, and the original operation result or exception is
preserved. rboto does not configure a logger for callback errors.

Cancelling the caller stops the operation and produces a cancellation event.
Callbacks are awaited during cancellation handling too, so avoid unbounded
waits in cleanup.

If the native operation has already completed and you cancel during terminal
callback delivery, remaining callbacks receive that settled outcome and
cancellation then propagates to the caller. The interrupted callback is not
restarted, and no second terminal event is emitted.

## Streaming responses

A `success` event means the operation returned its result. For `GetObject`, the
body may still be unread. Reading a byte stream or event stream, or encountering
a later stream error, does not generate another operation event.

Interceptors never consume or copy stream contents. Read and handle the stream
normally in your application.
