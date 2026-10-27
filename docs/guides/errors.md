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

`request_id` is populated when the response provides an identifier.

## Credentials, configuration, and transport

Import these exceptions from `rboto.exceptions`:

| Exception | Meaning |
|---|---|
| `CredentialsError` | A credential/token provider could not load or refresh credentials |
| `ConfigurationError` | Invalid client settings, missing region, or request construction failure |
| `ConnectionError` | The request could not reach the service |
| `TimeoutError` | A connection, request attempt, or SDK operation exceeded its timeout |
| `ResponseError` | The service response could not be decoded |

`TimeoutError` inherits from rboto's `ConnectionError`. An `asyncio.timeout()`
deadline raises Python's built-in `TimeoutError` instead. Server-side authentication
or permission rejections retain their service exception classes and codes.

Credential values and request bodies are not included in transport error messages.

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
