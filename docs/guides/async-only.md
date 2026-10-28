# Async-only

Every rboto service operation is an `async def` method. There are no synchronous
operation variants or `sync_` methods.

While a request waits for the network, the Python event loop can run other
coroutines. Sequential `await` calls still run one at a time. Your application
chooses which requests can run concurrently.

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
The current clients do not implement context managers or a `close()` method.

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

For streams, keep iteration inside the timeout block if the deadline must include
reading the body. Cancellation does not prove that a remote write was rejected.
Use the operation's idempotency token when available and account for an uncertain
result before repeating a write.
