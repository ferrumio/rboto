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
| `response` | `Error.Code`, `Error.Message`, and optional `RequestId` |

Unknown codes fall back to the service base exception and retain the original code
and operation name.

`request_id` is populated when supplied by the service. Failures outside a service
response use shared exceptions from `rboto.exceptions`:

| Exception | Meaning |
|---|---|
| `ConfigurationError` | Invalid settings, missing region, or request construction failure |
| `CredentialsError` | Credentials could not be loaded or refreshed |
| `ConnectionError` | Connection, proxy, or TLS failure |
| `TimeoutError` | Connection, read, attempt, or operation deadline |
| `ResponseError` | The service response could not be decoded |

`TimeoutError` inherits from `ConnectionError`; both and `ResponseError` inherit
from `ClientError`. `ConfigurationError` and `CredentialsError` inherit directly
from `RbotoError`. Transport messages do not include request bodies or signing
headers.

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
