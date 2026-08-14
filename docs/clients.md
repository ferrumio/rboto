# Clients

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
The client exposes all 106 modeled operations, including multipart operations and the
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

The DynamoDB client exposes all 57 operations as immutable native PyO3 outputs. Nested
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
