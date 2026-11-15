# Async-only

Every rboto service operation is an `async def` method. There are no synchronous
operation variants or `sync_` methods.

While a request waits for the network, the Python event loop can run other
coroutines. Sequential `await` calls still run one at a time. Your application
chooses which requests can run concurrently.

For the native worker pools shared by clients of each service, see
[runtime workers](runtime.md).

## asyncio, Trio, and AnyIO

rboto uses the scheduler of the calling task. Client methods, typed responses,
paginators, waiters, and streams have the same API on asyncio and Trio.
Native network operations run on Tokio with either Python scheduler.

asyncio requires no additional runtime dependency. For Trio, install its extra
alongside the services you need:

```bash
pip install "rboto[s3,trio]"
```

```python
--8<-- "examples/docs/trio_request.py"
```

With AnyIO, call the same async function using
`anyio.run(main, backend="asyncio")` or `anyio.run(main, backend="trio")`.
Install AnyIO separately; the Trio backend also needs the `trio` extra.
The tested dependency baselines are Trio 0.35 and AnyIO 4.16.

Use concurrency and cancellation primitives from the active scheduler. For
example, use a Trio nursery or an AnyIO task group when running on Trio.
Application interceptors and input iterators must also support that scheduler.

## Event-loop work

Async network I/O does not make every part of a call asynchronous. rboto still
does synchronous work when reading Python inputs and exposing Python results.
Large inputs, large collections, and application callbacks can delay other tasks.

| Work | Where it runs | Effect on the Python event loop |
| --- | --- | --- |
| Build parameters; copy input strings/bytes; convert nested inputs | Calling thread, with the GIL on standard CPython | Occupies the Python scheduler's thread |
| Initialize the worker pool on first use | Calling thread; pool construction releases the GIL | Startup still occupies the calling thread |
| Resolve credentials, send requests, wait for retries or response bodies | Native runtime | Network waits do not occupy the loop thread |
| Serialize requests, decode responses, prepare native output ownership | Native runtime, without the GIL | Uses native CPU; can compete for CPU resources |
| Deliver a Python result or create a stream chunk/full-body `bytes` value | Native worker, with the GIL | Python allocation/copying can compete with the loop for the GIL |
| Read output properties; create lists/dictionaries; call `to_dict()` | Calling thread, with the GIL | Synchronous; cost grows with the data exposed |
| Release output objects | Thread dropping the last reference | Freeing a large object graph also takes synchronous work |
| Format, write and flush built-in JSON logs | Calling thread | A slow destination delays the loop |
| Run application interceptors or validate Pydantic/dataclass models | Application code | Depends on the work performed |

Read the fields you need and keep request concurrency bounded. Reuse clients
to avoid repeated initialization. Accessing a collection of native objects does
not copy their large fields, but reading a string or bytes property still creates
a Python value. `to_dict()` explicitly converts the entire result.

For logging, use a destination that can keep up with the request rate. Moving
synchronous code to another thread does not guarantee better responsiveness:
work that holds the GIL can still delay Python tasks.

## Scripts

Put requests in an async function and start the event loop once:

```python
--8<-- "examples/docs/first_request.py"
```

Constructing the client does not require `await`. Calling an operation without
awaiting or scheduling it creates a coroutine and does not send the request.

## Applications and notebooks

Inside an async handler, job, or notebook with an active event loop, await the
operation directly. Do not call `asyncio.run()` inside an already running loop.

```python
--8<-- "examples/docs/read_parameter.py"
```

Create clients during application setup and pass them to code that needs them.
Use `async with` or `await client.aclose()` when their work is finished. See
[closing clients and streams](lifecycle.md) for ownership and cancellation.

For a synchronous Lambda entry point that calls async code, see
[AWS Lambda](aws-lambda.md).

## Concurrent requests

Use `asyncio.gather()` for a small, known set of independent operations:

```python
--8<-- "examples/docs/concurrent_requests.py"
```

Both calls reuse one client. Concurrency does not change service quotas or make
an individual request inherently faster.

For large inputs, keep a bounded number of tasks in flight. A semaphore limits
active calls, but creating one task for every input still consumes memory. Use
bounded batches or a fixed set of workers.

## Deadlines

Use `asyncio.timeout()` to bound your coroutine's wait:

```python
--8<-- "examples/docs/deadline.py"
```

On Trio, use `with trio.fail_after(seconds):`; on AnyIO, use
`with anyio.fail_after(seconds):`. Cancellation interrupts native work and stops
pending Python input polls. Closing a client or stream stays terminal even if
the wait for cleanup is cancelled. Use a shielded cancellation scope around
`await resource.aclose()` when cleanup must finish inside an already cancelled
Trio or AnyIO scope.

For streams, keep iteration inside the timeout block if the deadline must include
reading the body. Cancellation does not prove that a remote write was rejected.
Use the operation's idempotency token when available and account for an uncertain
result before repeating a write.
