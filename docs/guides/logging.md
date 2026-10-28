# Logging

Add `LoggingInterceptor()` to a client or Session to log operation metadata through
Python's standard `logging` module. It works with every service client and does not
require another package.

```python
from rboto import DynamoDBClient, LoggingInterceptor

client = DynamoDBClient(interceptors=[LoggingInterceptor()])
```

rboto does not install handlers, set logger levels, change propagation, or configure
the root logger. Your application chooses the format and destination.

## Enable logs and set service levels

Logger names follow `rboto.<service>`, using the service identifier from the
client catalog: `rboto.s3`, `rboto.dynamodb`, `rboto.bedrock-runtime`, and so on.
Configure `rboto` for all clients or a child logger for one service.

This example runs two operations concurrently. DynamoDB logs completion and retry
diagnostics; S3 logs failures and cancellation. Each task has its own correlation ID.

```python
--8<-- "examples/docs/logging_operations.py"
```

The `%(rboto)s` formatter field contains the operation metadata. The formatter's
default empty dictionary also lets it handle records from other libraries.

| Level | Event | When emitted |
| --- | --- | --- |
| `INFO` | `operation.completed` | The downstream chain returns successfully |
| `ERROR` | `operation.completed` | The downstream chain raises an exception |
| `WARNING` | `operation.completed` | The operation is cancelled |
| `DEBUG` | `operation.retry` | Native dispatch made more than one attempt |

There is one completion record per invocation when its level is enabled. The
optional retry record is a summary emitted after dispatch, with up to 32 attempt
details. It is not a live notification of each retry.

Without the interceptor, no operation logs are produced. A logger level above
`ERROR`, such as `CRITICAL`, disables all its records and skips timing and field
preparation. The interceptor chain and native metadata collection still run.
To remove that overhead too, omit the interceptor or pass `interceptors=()` to
override Session inheritance.

## Format records as JSON

The standard `LogRecord` carries a dictionary in `record.rboto`. rboto does not
serialize it or select a JSON logging library:

```python
--8<-- "examples/docs/logging_json.py"
```

Place a handler at one point in the logger hierarchy, typically the root logger.
Adding handlers to both `rboto` and the root while propagation is enabled can
duplicate output. rboto itself does not add handlers.

## Fields

`rboto.logging.OperationLogFields` describes the dictionary for typed formatters.
Fields are present with `None` when unavailable, except the optional trace and
retry details listed below.

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `event` | `operation.completed` or `operation.retry` |
| `service`, `operation` | Service identifier and modeled operation name |
| `operation_id` | Unique ID of this client invocation |
| `correlation_id` | Application ID set by `operation_context()` |
| `region` | Resolved native region when available |
| `duration_ms` | Elapsed milliseconds around the downstream chain, including its middleware |
| `outcome` | `success`, `error`, or `cancelled` |
| `dispatched` | Whether the native adapter was called |
| `native_duration_ms` | Native call duration, including initialization and retries |
| `attempt_count`, `retry_count` | Native attempts and retries; zero before dispatch, `None` when unknown |
| `status_code`, `request_id` | Last available native HTTP status and request ID |
| `error_code`, `error_type` | Final exception's modeled code and class name |
| `native_error_code`, `native_error_type` | Native failure, even if downstream middleware recovered |
| `trace_id`, `span_id` | Optional application-supplied trace identifiers |
| `attempts` | Retry record only: attempt number, `duration_ms`, status and request ID |
| `attempts_truncated` | Retry record only: whether detailed attempts were omitted |

Attempt duration excludes backoff before the next attempt. Cancellation may prevent
the native collector from returning attempt metadata; unknown counts stay `None`.

The logger never reads parameters, response properties, streams, `to_dict()`,
exception messages, or tracebacks. Logs exclude credentials, authorization headers,
resource names, request/response bodies, and unrestricted error text. Correlation
and trace IDs are supplied by your application: use identifiers appropriate for logs.

## Order with other middleware

Register logging first to include cache hits, policy failures, response replacement,
and error recovery in its outcome:

```python
client = DynamoDBClient(interceptors=[LoggingInterceptor(), cache, policy])
```

If `cache` returns without forwarding, the log reports `success`, `dispatched=False`
and zero attempts. If `policy` raises before sending, it reports `error` with no
native dispatch. If middleware catches a native error and returns a fallback, the
outcome is `success` while `native_error_code` still describes the native failure.

Putting logging after a cache or policy means it sees only calls forwarded to it.
For [streaming outputs](streaming.md), completion means the operation returned its
output. It does not measure later stream consumption or log later stream failures.

Ordinary exceptions from application log handlers, filters, or trace lookup do not
replace an operation result or hide its exception. Failed trace lookup omits the
trace fields. Failed log delivery is not retried. Use this for diagnostics, not as
guaranteed audit delivery. Handlers run synchronously; use the standard
`QueueHandler`/`QueueListener` in your application for slow destinations.

## Attach trace IDs

`LoggingInterceptor(trace_context=...)` accepts a synchronous provider returning
`LogTraceContext` or `None`. It is called on the operation's task only when a
record's logger level is enabled. This example uses a `ContextVar` to represent
application tracing:

```python
--8<-- "examples/docs/logging_trace.py"
```

Adapt the provider to your tracer's current span. Keep that span active while
logging runs; a tracing middleware normally belongs outside the logging middleware.
This hook only copies IDs and does not create spans or require OpenTelemetry.

## AWS Lambda

Create the client outside the handler and attach the invocation's request ID with
`operation_context()`. The example uses the runtime's existing logging handlers.

Set the function's application log format to **JSON** and application log level
to **INFO** in Lambda's logging configuration. Its JSON handler includes the
`rboto` dictionary passed through `extra`; the default text formatter does not
display that metadata. See
[logging extra parameters in Lambda](https://docs.aws.amazon.com/lambda/latest/dg/python-logging.html).

```python
--8<-- "examples/docs/logging_lambda.py"
```

The handler is synchronous and waits for the async operation. Context is restored
on exit, including errors, so reused execution environments do not retain the
previous invocation's correlation ID. See [AWS Lambda](aws-lambda.md) for packaging
and client reuse.
