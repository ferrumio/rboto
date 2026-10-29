# Logging

Call `configure_logging()` once to enable JSON logs for every rboto client.
No handler, formatter, or interceptor registration is required.

```python
from rboto import S3Client, configure_logging

configure_logging()
client = S3Client()
await client.list_buckets()
```

Logs go to stdout. Each service has its own logger name: `rboto.s3`,
`rboto.dynamodb`, `rboto.bedrock-runtime`, and so on. The name is also available
as `client.logger_name`.

Every operation log includes a UTC `timestamp`, `level`, `logger`, `service`,
`operation`, `message`, and operation metadata. JSON is formatted automatically,
including when stdout is collected by a container or Lambda.

## Set levels and disable services

Use one configuration for the whole application:

```python
configure_logging(
    level="INFO",
    service_levels={"s3": "OFF", "dynamodb": "DEBUG"},
)
```

This disables S3 logs, enables DynamoDB retry diagnostics, and uses INFO for the
other services. Settings apply to existing clients, future clients, Session-based
clients, and compatibility factories.

Calling `configure_logging()` again replaces the default level and per-service
overrides. It resets rboto service loggers to that configuration and reuses its
handler; it does not add duplicate handlers. The application root logger and its
handlers are unchanged. rboto records do not propagate to the root handler.

To disable all operation logging:

```python
configure_logging(level="OFF")
```

To enable only S3:

```python
configure_logging(level="OFF", service_levels={"s3": "INFO"})
```

For a change to one logger without reconfiguring the others, use `get_logger()`.
It returns the standard Python logger with the service's name:

```python
--8<-- "examples/docs/logging_control.py"
```

`get_logger("s3")` and `logging.getLogger("rboto.s3")` return the same object.
Disabling that logger skips automatic logging and its native metadata collection.
Custom application middleware, if configured, still runs.

No operation logs are produced until `configure_logging()` is called. Without
logging or application middleware, operations use direct native dispatch.

## What gets logged

| Level | Event | Meaning |
| --- | --- | --- |
| `INFO` | `operation.completed` | The operation returns successfully |
| `ERROR` | `operation.completed` | The operation raises an exception |
| `WARNING` | `operation.completed` | The operation is cancelled |
| `DEBUG` | `operation.retry` | Native dispatch made more than one attempt |

There is one completion record per invocation when its level is enabled. A retry
record summarizes up to 32 completed attempts after native dispatch finishes.

Logging automatically wraps application middleware, so cache hits, policy blocks,
and error recovery are included. A cache hit has `dispatched=false` and zero native
attempts. A policy failure is an error even if no request was sent. If middleware
recovers from a native error, the final outcome is successful while the native error
fields retain the underlying failure.

For streaming operations, completion means the response is available. Logging does
not read the stream or measure later consumption; subsequent stream errors remain
with the code consuming it.

## JSON fields

| Field | Meaning |
| --- | --- |
| `timestamp`, `level`, `logger` | UTC time, log level, and service logger name |
| `message`, `event` | `operation.completed` or `operation.retry` |
| `schema_version` | `1` |
| `service`, `operation` | Service identifier and modeled operation name |
| `operation_id` | Unique ID for this invocation |
| `correlation_id` | Application ID from `operation_context()` |
| `region` | Resolved native region when available |
| `duration_ms` | Total elapsed milliseconds, including application middleware |
| `outcome` | `success`, `error`, or `cancelled` |
| `dispatched` | Whether the native adapter was called |
| `native_duration_ms` | Native call duration, including initialization and retries |
| `attempt_count`, `retry_count` | Native attempts/retries; zero before dispatch, null when unknown |
| `status_code`, `request_id` | Last available native HTTP status and request ID |
| `error_code`, `error_type` | Final exception's modeled code and class name |
| `native_error_code`, `native_error_type` | Underlying native failure, including recovered failures |
| `trace_id`, `span_id` | Optional application-supplied trace IDs |
| `attempts` | Retry records only: number, duration in milliseconds, status and request ID |
| `attempts_truncated` | Retry records only: whether detailed attempts were omitted |

Unavailable values are null. Trace and retry detail fields are omitted when not
applicable. Attempt duration excludes retry backoff. Cancellation can prevent the
native collector from delivering attempt counts, so unknown counts remain null.

Automatic operation logs never inspect parameters, response properties, streams,
`to_dict()`, exception messages, or tracebacks. Correlation and trace IDs are supplied
by your application; use IDs appropriate for logs.

## Concurrent calls and correlation

Each task keeps its own operation and correlation IDs:

```python
--8<-- "examples/docs/logging_operations.py"
```

## Trace IDs

An optional synchronous provider can return `LogTraceContext` or `None`. The
provider runs on the operation's task when the record's level is enabled. It does
not require a telemetry dependency:

```python
--8<-- "examples/docs/logging_trace.py"
```

Use your tracer's active span IDs in the provider. No spans are created by logging.

## AWS Lambda

Configure logging once, outside the handler. Attach the invocation ID with
`operation_context()` and keep the client available for reuse:

```python
--8<-- "examples/docs/logging_lambda.py"
```

The output is already JSON; no runtime formatter is needed. If you enable Lambda
log-level filtering, choose a level that retains the rboto records you want.
Context is restored on exit, including failures, so reused environments do not
retain a previous invocation's correlation ID. See [AWS Lambda](aws-lambda.md) for
packaging and client reuse.

## Output destination

Stdout is the default. Pass a text stream when the application needs a different
destination, such as a file it manages:

```python
with open("operations.jsonl", "a", encoding="utf-8") as output:
    configure_logging(stream=output)
    await client.list_buckets()
    configure_logging(level="OFF")
```

rboto does not close application-owned streams. Writes are synchronous; a slow
destination can delay the caller. Ordinary logging failures do not replace the
operation result or its original exception. Delivery is not retried, so use these
records for diagnostics rather than guaranteed audit delivery.
