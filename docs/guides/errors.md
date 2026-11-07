# Errors

## Input conversion errors

When an input cannot be converted, the error includes the field that failed:

```text
TypeError: messages[0].content[1].text: expected str (bedrock-runtime.Converse; type)
```

The exception keeps its existing class, such as `TypeError`, `ValueError` or
`OverflowError`. Read its typed context instead of parsing the message:

```python
from rboto import BedrockRuntimeClient
from rboto.exceptions import get_conversion_context
from rboto_bedrock_runtime.types import Message


async def converse(messages: list[Message]) -> None:
    client = BedrockRuntimeClient(region="eu-west-2")
    try:
        await client.converse(model_id="your-model-id", messages=messages)
    except (TypeError, ValueError, OverflowError) as error:
        context = get_conversion_context(error)
        if context is not None:
            print(context.path, context.expected, context.category)
        raise
```

`get_conversion_context()` returns a frozen `ConversionErrorContext`, or `None`
for errors without conversion metadata.

| Field | Meaning |
|---|---|
| `service` | Service ID, such as `bedrock-runtime` |
| `operation` | Modeled name, such as `Converse` |
| `path` | Python field names and container positions |
| `expected` | Expected type or conversion rule |
| `category` | `type`, `value`, `missing`, `union`, `overflow`, `iteration` or `unknown_parameter` |
| `truncated` | Whether a context field exceeded its length limit |

Paths use zero-based indexes. For dictionaries with arbitrary keys,
`item[value#0].l[1].s` means the first map entry's value, then its `l` member,
second list element and `s` member. `[key#0]` identifies the first entry's key.
Map positions follow the input dictionary's iteration order.

Diagnostics never include input values or arbitrary map keys. Paths are limited
to 512 characters; expected rules to 160; service and operation names to 128 each.
If application code raises an exception during conversion, rboto preserves its
message and cause and adds a contextual note. Application-provided messages and
causes can still contain sensitive data.

This reports failures the converter already detects, including invalid unions
and missing required nested members. It does not add validation of every service
constraint. Errors from Python argument binding, deferred event-stream items,
SDK request construction and service responses retain their existing behavior.
An input conversion failure happens before that operation sends a request.

## Service errors

Catch a modeled service exception when you need to handle a specific failure:

```python
--8<-- "examples/docs/handle_errors.py"
```

`NoSuchKeyError` handles that S3 response. `S3Error` catches other S3 service
failures. Other services follow the same pattern, for example
`rboto_dynamodb.exceptions.ConditionalCheckFailedError`.

## Error fields

Service exceptions inherit from `rboto.exceptions.ClientError`.

| Attribute | Meaning |
|---|---|
| `error_code` | Original service code |
| `message` | Error message |
| `operation_name` | Operation, such as `GetObject` |
| `request_id` | Request identifier when populated |
| `response_metadata` | Typed, allowlisted HTTP and retry context |
| `details` | Additional decoded fields of the modeled error |
| `response` | `Error.Code`, `Error.Message`, optional `RequestId`, and `ResponseMetadata` |

Unknown codes fall back to the service base exception and retain the original code
and operation name.

`request_id` is populated when supplied by the service. Failures outside a service
response use shared exceptions from `rboto.exceptions`:

| Exception | Meaning |
|---|---|
| `ConfigurationError` | Invalid settings, missing region, or request construction failure |
| `CredentialsError` | Credentials could not be loaded or refreshed |
| `ConnectionError` | Connection, proxy, or TLS failure |
| `TimeoutError` | Native connection, read, attempt, operation, or stream timeout |
| `ResponseError` | Response decoding, validation, or an otherwise unclassified body failure |

`TimeoutError` inherits from `ConnectionError`; both and `ResponseError` inherit
from `ClientError`. `ConfigurationError` and `CredentialsError` inherit directly
from `RbotoError`. Transport messages do not include request bodies or signing
headers.

### Response metadata

`error.response_metadata` and `error.response["ResponseMetadata"]` expose the same
`ResponseMetadata` typed dictionary from `rboto.exceptions`:

| Key | Type | Meaning |
|---|---|---|
| `HTTPStatusCode` | `int \| None` | Status of the final HTTP response |
| `RequestId` | `str \| None` | Service request ID; also available as `error.request_id` |
| `HostId` | `str \| None` | Extended S3 request ID, when supplied |
| `RetryAttempts` | `int \| None` | Native retries made before returning, excluding the initial attempt |

Unknown values are `None`. Native dispatch records retries even when logging,
interceptors and tracing are disabled. An exception constructed by application
code has unknown retry metadata unless that code supplies it. A connection
failure before headers has no HTTP status or request ID. Metadata describes the
final attempt; an earlier retry's response does not substitute for a missing final
response.

Only request ID headers and HTTP status are retained. rboto does not expose a
header dump, cookies, authorization, or the raw error body. Request IDs with
whitespace, non-ASCII characters, or more than 1,024 bytes are omitted.

### Modeled details

Additional modeled fields are available through `details`, with a generated
`TypedDict` specific to the exception. Field names use the same Python spelling
as client inputs and outputs. Missing fields are omitted:

```python
from rboto_dynamodb.exceptions import ConditionalCheckFailedError

try:
    await client.put_item(
        table_name="jobs",
        item={"pk": {"s": "job-123"}, "status": {"s": "running"}},
        condition_expression="attribute_not_exists(pk)",
        return_values_on_condition_check_failure="ALL_OLD",
    )
except ConditionalCheckFailedError as error:
    previous = error.details.get("item")
    print(error.response_metadata["HTTPStatusCode"])
    # previous is a typed mapping of attribute names to AttributeValueOutput.
```

For a `ModelStreamError`, `details.get("original_status_code")` returns
`int | None`. Its schema is available as
`rboto_bedrock_runtime.types.ModelStreamErrorDetails`.

Nested structures in error details are dictionaries, with the corresponding
generated output types. Unknown service errors have empty details; unknown
payload fields are ignored. These fields can contain application data, such as
the item returned by a failed DynamoDB condition. rboto keeps them out of
`str(error)`, `repr(error)`, response metadata, automatic logging and tracing.
Service-provided error messages and application exceptions can still contain
sensitive data.

## Errors while reading streams

The same shared exceptions apply before and after an operation returns:

```python
from rboto.exceptions import ConnectionError, ResponseError

try:
    output = await s3.get_object(bucket="my-bucket", key="report.csv")
    async with output.body as body:
        async for chunk in body:
            await consume(chunk)
except ConnectionError as error:
    print(error.operation_name, error.request_id)
except ResponseError as error:
    print(error.response_metadata["HTTPStatusCode"])
```

Both byte iteration and `read()` normalize transport failures, recognizable
native timeouts, and decoding/validation failures. This replaces the earlier
`OSError` from byte streams. A failed checksum raises `ResponseError`.
Event receivers retain modeled exception classes, including their typed details.
An invalid event frame or payload raises `ResponseError`; a connection interrupted
while receiving events raises `ConnectionError`.

Returned streams retain their operation and initial HTTP response context even
after the client closes. A stream error can therefore have HTTP status `200`:
the request succeeded before consumption failed. Retry metadata covers the
original operation. rboto does not retry or replay a partially consumed response.
After a terminal stream failure, iteration is exhausted and a byte `read()`
returns empty bytes.

Classification uses typed native causes, not exception text. Some SDK body
failures expose only an opaque private error, including the SDK's minimum
download throughput check. Those raise `ResponseError` rather than guessing a
timeout from their message.

Client operation/attempt/read timeouts do not introduce a deadline around later
response consumption. Use `asyncio.timeout()` for an application deadline;
its `TimeoutError` remains Python's built-in exception. `asyncio.CancelledError`
is never converted to an rboto network error. Exceptions from Python upload or
event producers retain their original identity, traceback and cause.

Explicit closure retains the [lifecycle contract](lifecycle.md): closing a pending
byte collection raises `StreamClosedError`, closing a pending iterator ends it,
and entering a closed stream raises `StreamClosedError`. These are separate from
transport failures. Input conversion continues to raise the documented
`TypeError`, `ValueError`, `OverflowError`, or original application exception.

## Partial failures

A successful API response does not always mean all requested work succeeded.

| Operation | Inspect |
|---|---|
| EventBridge `put_events()` | `failed_entry_count`, entry errors |
| SQS batch operations | `failed` entries |
| DynamoDB `batch_write_item()` | `unprocessed_items` |
| Lambda `invoke()` | `function_error`, payload |
| Step Functions `start_sync_execution()` | `status`, `error`, `cause` |

Retry failed work according to the operation's contract. Repeating an entire
batch can duplicate entries that succeeded.

The SDK already retries eligible request failures. Additional retries multiply
attempts. A timed-out write can have an uncertain outcome; use idempotency tokens
where available and check resource state when your workflow requires it.
