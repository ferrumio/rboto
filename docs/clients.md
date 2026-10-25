# Clients

## Service catalog

rboto implements 26 services and 1,309 operations, covering the AWS SDK for Python
service catalog plus S3. Install each service using `pip install "rboto[<service>]"`,
or use `pip install "rboto[all]"` for the complete catalog. Each service also has an
independent `rboto-<service>` package.

| Service / extra | Factory | Operations |
|---|---|---:|
| `api-gateway` | `api_gateway()` | 124 |
| `bedrock` | `bedrock()` | 108 |
| `bedrock-agent` | `bedrock_agent()` | 86 |
| `bedrock-agent-runtime` | `bedrock_agent_runtime()` | 35 |
| `bedrock-agentcore` | `bedrock_agentcore()` | 67 |
| `bedrock-agentcore-control` | `bedrock_agentcore_control()` | 171 |
| `bedrock-data-automation` | `bedrock_data_automation()` | 27 |
| `bedrock-data-automation-runtime` | `bedrock_data_automation_runtime()` | 6 |
| `bedrock-runtime` | `bedrock_runtime()` | 11 |
| `cloudtrail` | `cloudtrail()` | 60 |
| `cognito-identity` | `cognito_identity()` | 23 |
| `connecthealth` | `connecthealth()` | 16 |
| `dynamodb` | `dynamodb()` | 58 |
| `guardduty` | `guardduty()` | 102 |
| `lambda` | `lambda_()` | 88 |
| `lex-runtime-v2` | `lex_runtime_v2()` | 6 |
| `polly` | `polly()` | 10 |
| `qbusiness` | `qbusiness()` | 83 |
| `s3` | `s3()` | 112 |
| `sagemaker-runtime-http2` | `sagemaker_runtime_http2()` | 1 |
| `secrets-manager` | `secrets_manager()` | 23 |
| `signin` | `signin()` | 11 |
| `sns` | `sns()` | 42 |
| `sqs` | `sqs()` | 23 |
| `sts` | `sts()` | 11 |
| `transcribe-streaming` | `transcribe_streaming()` | 5 |

Hyphens become underscores in Python imports and factory names. For example,
`rboto-bedrock-runtime` exports `rboto_bedrock_runtime.BedrockRuntimeClient` and
`rboto.bedrock_runtime()`. Use `rboto.lambda_()` for Lambda.

```python
from rboto import secrets_manager, sts

async def load_secret() -> str | None:
    identity = await sts(region="us-east-1").get_caller_identity()
    print(identity.account)
    secret = await secrets_manager(region="us-east-1").get_secret_value(
        secret_id="my-secret",
    )
    return secret.secret_string
```

### Documents and event streams

Smithy document inputs accept JSON values: `None`, booleans, integers, finite floats,
strings, lists, and dictionaries with string keys. Their `JSONValue` type alias is
recursive. Tagged unions accept exactly one modeled variant per value.

Bidirectional operations accept typed Python async iterators. Input events are pulled
as the Rust SDK sends them; output events remain independently consumable async
iterators. A streaming response uses a typed mapping containing its receiver.

```python
from collections.abc import AsyncIterator
from rboto import bedrock_runtime
from rboto_bedrock_runtime.types import InvokeModelWithBidirectionalStreamInput

async def events() -> AsyncIterator[InvokeModelWithBidirectionalStreamInput]:
    yield {"chunk": {"bytes": b"model-specific request payload"}}

async def stream(model_id: str) -> None:
    response = await bedrock_runtime(region="us-east-1").invoke_model_with_bidirectional_stream(
        model_id=model_id,
        body=events(),
    )
    async for event in response["body"]:
        print(event)
```

The payload format depends on the selected model. Modeled event-stream failures raise
the service's generated exception subclasses, just like regular operation failures.
Byte-stream responses retain their native stream ownership; `read()` consumes the
remaining body, while `async for` processes it incrementally.


Each AWS service is distributed as an independent native wheel while sharing the small
`rboto` Python facade.

## Amazon S3

Install and create the client:

```bash
pip install "rboto[s3]"
```

```python
from rboto import s3

client = s3(region="us-east-1")
```

### Upload and stream an object

```python
await client.put_object(
    bucket="my-bucket",
    key="data.bin",
    body=b"hello from rboto",
    content_type="application/octet-stream",
)

response = await client.get_object(
    bucket="my-bucket",
    key="data.bin",
)

async for chunk in response.body:
    process(chunk)
```

`response.body` is a `ByteStream`. It supports incremental iteration and an
aggregating convenience method:

```python
response = await client.get_object(bucket="my-bucket", key="data.bin")
data: bytes = await response.body.read()
```

Multiple streams can progress concurrently:

```python
import asyncio

responses = await asyncio.gather(
    client.get_object(bucket="my-bucket", key="one.bin"),
    client.get_object(bucket="my-bucket", key="two.bin"),
)

one, two = await asyncio.gather(
    responses[0].body.read(),
    responses[1].body.read(),
)
```

S3 responses are immutable native PyO3 objects with typed properties and `to_dict()`.
The client exposes all 112 modeled operations, including multipart operations and the
specialized `SelectObjectContent` event stream.

## Amazon SQS

```bash
pip install "rboto[sqs]"
```

```python
from rboto import sqs

client = sqs(region="us-east-1")
```

SQS responses are immutable native PyO3 objects with typed properties. Nested
structures, such as received messages, are native typed objects as well. Every output
also provides `to_dict()` for interoperability.

### Send and receive a message

```python
sent = await client.send_message(
    queue_url=queue_url,
    message_body="hello from rboto",
    message_attributes={
        "source": {
            "data_type": "String",
            "string_value": "documentation",
        }
    },
)
print(sent.message_id)

received = await client.receive_message(
    queue_url=queue_url,
    max_number_of_messages=10,
    wait_time_seconds=10,
)

for message in received.messages or []:
    print(message.body)
    receipt_handle = message.receipt_handle
    if receipt_handle is not None:
        await client.delete_message(
            queue_url=queue_url,
            receipt_handle=receipt_handle,
        )
```

The SQS client exposes all 23 operations from its pinned Smithy model and Rust crate.

## Amazon SNS

```bash
pip install "rboto[sns]"
```

```python
from rboto import sns

client = sns(region="us-east-1")
```

### Create a topic and publish

```python
created = await client.create_topic(
    name="events",
    tags=[{"key": "application", "value": "example"}],
)
if created.topic_arn is None:
    raise RuntimeError("SNS did not return a topic ARN")

published = await client.publish(
    topic_arn=created.topic_arn,
    subject="New event",
    message="hello from rboto",
)
print(published.message_id)
```

SNS responses and nested topic, subscription, endpoint, and tag structures are immutable
native PyO3 objects. The SNS client exposes all 42 operations from its pinned model.

## Amazon DynamoDB

```bash
pip install "rboto[dynamodb]"
```

```python
from rboto import dynamodb

client = dynamodb(region="us-east-1")
```

### Put and get an item

DynamoDB currently uses the explicit, strictly typed `AttributeValue` representation:

```python
from rboto_dynamodb.types import AttributeValue

item: dict[str, AttributeValue] = {
    "pk": {"s": "USER#1"},
    "name": {"s": "Ada"},
    "age": {"n": "37"},
    "active": {"bool": True},
    "roles": {"ss": ["admin", "developer"]},
}

await client.put_item(
    table_name="users",
    item=item,
)

response = await client.get_item(
    table_name="users",
    key={"pk": {"s": "USER#1"}},
)
print(response.item)
```

### Query

```python
response = await client.query(
    table_name="users",
    key_condition_expression="pk = :pk",
    expression_attribute_values={
        ":pk": {"s": "USER#1"},
    },
)

for item in response.items or []:
    print(item)
```

The DynamoDB client exposes all 58 operations as immutable native PyO3 outputs. Nested
AWS structures are typed objects, while `AttributeValue` remains an exact tagged union.

## Error handling

Every service has a generated exception hierarchy:

```python
from rboto import s3
from rboto_s3.exceptions import NoSuchKeyError, S3Error

client = s3(region="us-east-1")

try:
    await client.get_object(bucket="my-bucket", key="missing.bin")
except NoSuchKeyError:
    print("Object not found")
except S3Error as error:
    print(error.error_code, error.request_id)
```

Unknown AWS error codes fall back to the service base exception while preserving the
original code and operation name.
