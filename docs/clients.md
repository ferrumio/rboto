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

async for chunk in response["body"]:
    process(chunk)
```

`response["body"]` is a `ByteStream`. It supports incremental iteration and an
aggregating convenience method:

```python
response = await client.get_object(bucket="my-bucket", key="data.bin")
data: bytes = await response["body"].read()
```

Multiple streams can progress concurrently:

```python
import asyncio

responses = await asyncio.gather(
    client.get_object(bucket="my-bucket", key="one.bin"),
    client.get_object(bucket="my-bucket", key="two.bin"),
)

one, two = await asyncio.gather(
    responses[0]["body"].read(),
    responses[1]["body"].read(),
)
```

The S3 client currently exposes all 106 operations modeled by its pinned AWS SDK
release, including multipart and `SelectObjectContent` event streaming operations.

## Amazon SQS

```bash
pip install "rboto[sqs]"
```

```python
from rboto import sqs

client = sqs(region="us-east-1")
```

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
print(sent.get("message_id"))

received = await client.receive_message(
    queue_url=queue_url,
    max_number_of_messages=10,
    wait_time_seconds=10,
)

for message in received.get("messages", []):
    print(message.get("body"))
    receipt_handle = message.get("receipt_handle")
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
print(response.get("item"))
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

for item in response.get("items", []):
    print(item)
```

The DynamoDB client exposes all 57 operations from its pinned model. Automatic native
Python value conversion is planned as a service customization; the current tagged union
keeps the generated contract exact and explicit.

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
