# Errors

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
