# Runtime workers

Each service uses its own Tokio runtime to execute native async requests.
Clients of the same service share that runtime. Importing a service or creating
a client does not start its worker pool; the first native operation starts it.

The default is **at most two workers per used service**, or one when the native
runtime detects only one available CPU. To choose a different size:

```python
--8<-- "examples/docs/runtime_configuration.py"
```

With this setting, using S3 and SQS starts two pools of two workers. Creating a
second S3 client reuses the S3 pool. Workers remain alive until the process exits,
including after clients are discarded or an `asyncio.run()` call finishes.

## Configuration and precedence

Call `configure_runtime(worker_threads=...)` during application setup, before
starting native operations. It accepts a positive integer, excluding booleans.
The first native runtime initialization resolves and freezes one policy:

1. An explicit `configure_runtime()` setting.
2. The existing `TOKIO_WORKER_THREADS` environment variable.
3. The bounded default above.

All subsequently used services inherit that policy. You can change your explicit
setting before initialization. Afterward, repeating the effective count is
harmless; a different count raises `RuntimeError`. Later environment changes do
not resize existing pools or change the count used by another service.

An invalid environment value raises `ValueError` at first use. An explicit
setting takes precedence over it. The configuration function does not write
environment variables. Direct service imports, such as `from rboto_s3 import
S3Client`, use the same policy.

## Workers and request concurrency

Two workers can handle many requests that are waiting for network I/O. This
setting is **not a limit of two concurrent requests**, a process-wide thread
limit, or a setting for Python's event loop.

The native runtime can also create temporary blocking workers for tasks such as
DNS and certificate loading. These are separate from the async workers.
For request limits, use the application patterns in
[concurrent requests](async-only.md#concurrent-requests).

Applications that spend substantial native CPU time on large bodies may benefit
from more workers. Measure throughput, latency, CPU and memory with your workload
before increasing the count, especially when using several services.
