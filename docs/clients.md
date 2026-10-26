# Clients

## Service catalog

rboto implements 34 services and 1,865 operations.
Install each service using `pip install "rboto[<service>]"`,
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
| `cloudwatch-logs` | `cloudwatch_logs()` | 118 |
| `cognito-identity` | `cognito_identity()` | 23 |
| `connecthealth` | `connecthealth()` | 16 |
| `dynamodb` | `dynamodb()` | 58 |
| `eventbridge` | `eventbridge()` | 57 |
| `guardduty` | `guardduty()` | 102 |
| `kms` | `kms()` | 54 |
| `lambda` | `lambda_()` | 88 |
| `lex-runtime-v2` | `lex_runtime_v2()` | 6 |
| `pipes` | `pipes()` | 10 |
| `polly` | `polly()` | 10 |
| `qbusiness` | `qbusiness()` | 83 |
| `s3` | `s3()` | 112 |
| `sagemaker-runtime-http2` | `sagemaker_runtime_http2()` | 1 |
| `scheduler` | `scheduler()` | 12 |
| `secrets-manager` | `secrets_manager()` | 23 |
| `sesv2` | `sesv2()` | 116 |
| `sfn` | `sfn()` | 37 |
| `signin` | `signin()` | 11 |
| `sns` | `sns()` | 42 |
| `sqs` | `sqs()` | 23 |
| `ssm` | `ssm()` | 152 |
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

## Logs, configuration, encryption, workflows, and email

```bash
pip install "rboto[cloudwatch-logs,ssm,kms,sfn,sesv2]"
```

| Service | Factory | Examples |
|---|---|---|
| CloudWatch Logs | `cloudwatch_logs()` | Log groups, streams, queries, Live Tail |
| Systems Manager | `ssm()` | Parameter Store, documents, commands, automation |
| Key Management Service | `kms()` | Encryption, decryption, signatures, keys |
| Step Functions | `sfn()` | State machines and workflow executions |
| Simple Email Service v2 | `sesv2()` | Email content, templates, identities, configuration |

### CloudWatch Logs

Send events to an existing log group and stream:

```python
import time

from rboto import cloudwatch_logs
from rboto_cloudwatch_logs.types import InputLogEvent

async def write_log(group: str, stream: str, message: str) -> None:
    event: InputLogEvent = {"timestamp": int(time.time() * 1000), "message": message}
    response = await cloudwatch_logs(region="us-east-1").put_log_events(
        log_group_name=group,
        log_stream_name=stream,
        log_events=[event],
    )
    if response.rejected_log_events_info is not None:
        raise RuntimeError("One or more log events were rejected")
```

Live Tail exposes a typed asynchronous event stream. Stream-specific failures raise
`SessionStreamingError` or `SessionTimeoutError` from `rboto_cloudwatch_logs.exceptions`.

```python
from collections.abc import AsyncIterator

from rboto import cloudwatch_logs
from rboto_cloudwatch_logs.types import StartLiveTailResponseStream

async def tail_logs(group_arn: str) -> AsyncIterator[StartLiveTailResponseStream]:
    response = await cloudwatch_logs(region="us-east-1").start_live_tail(
        log_group_identifiers=[group_arn],
    )
    async for event in response["response_stream"]:
        yield event
```

### Systems Manager Parameter Store

```python
from rboto import ssm

async def read_parameter(name: str) -> str | None:
    response = await ssm(region="us-east-1").get_parameter(
        name=name,
        with_decryption=True,
    )
    return response.parameter.value if response.parameter is not None else None
```

Missing parameters raise `rboto_ssm.exceptions.ParameterNotFoundError`. A parameter
version or label can be appended to the name, such as `/app/config:3`.

### KMS

Binary inputs and outputs use `bytes`; rboto handles their wire encoding.

```python
from rboto import kms

async def encrypt_value(key_id: str, value: bytes) -> bytes:
    response = await kms(region="us-east-1").encrypt(
        key_id=key_id,
        plaintext=value,
        encryption_context={"application": "example"},
    )
    if response.ciphertext_blob is None:
        raise RuntimeError("KMS returned no ciphertext")
    return response.ciphertext_blob
```

Supply the same encryption context when decrypting the value.

### Step Functions

Step Functions uses the `sfn` service name. Workflow inputs and outputs are JSON strings.

```python
import json

from rboto import sfn

async def start_workflow(state_machine_arn: str, order_id: str) -> str:
    response = await sfn(region="us-east-1").start_execution(
        state_machine_arn=state_machine_arn,
        input=json.dumps({"order_id": order_id}),
    )
    return response.execution_arn
```

For synchronous Express executions, use `start_sync_execution()` and check `status`.
A failed workflow can return a normal API response with `status == "FAILED"` and
`error`/`cause` populated.

### SES v2

```python
from rboto import sesv2
from rboto_sesv2.types import EmailContent

async def send_message(sender: str, recipient: str, text: str) -> str | None:
    content: EmailContent = {
        "simple": {
            "subject": {"data": "Notification", "charset": "UTF-8"},
            "body": {"text": {"data": text, "charset": "UTF-8"}},
        }
    }
    response = await sesv2(region="us-east-1").send_email(
        from_email_address=sender,
        destination={"to_addresses": [recipient]},
        content=content,
    )
    return response.message_id
```

The sender must be an identity allowed by SES. Account sandbox and sending restrictions
still apply. Attachments and raw messages accept binary content as `bytes`.

## Amazon EventBridge, Scheduler, and Pipes

These are three independent clients:

| Client | Purpose |
|---|---|
| `eventbridge()` | Event buses, events, rules, targets, archives, and replays |
| `scheduler()` | One-time and recurring schedules, time zones, and flexible time windows |
| `pipes()` | Connect event sources to targets with filtering and optional enrichment |

Traditional scheduled rules belong to `eventbridge()`. EventBridge Scheduler has its own
`scheduler()` client.

```bash
pip install "rboto[eventbridge,scheduler,pipes]"
```

Publish a typed event to an existing bus:

```python
import json

from rboto import eventbridge
from rboto_eventbridge.types import PutEventsRequestEntry

async def publish_order(bus_name: str, order_id: str) -> list[str]:
    entry: PutEventsRequestEntry = {
        "event_bus_name": bus_name,
        "source": "myapp.orders",
        "detail_type": "OrderCreated",
        "detail": json.dumps({"order_id": order_id}),
    }
    response = await eventbridge(region="us-east-1").put_events(entries=[entry])
    entries = response.entries or []
    if response.failed_entry_count:
        failures = [
            (item.error_code, item.error_message)
            for item in entries
            if item.error_code is not None
        ]
        raise RuntimeError(f"Event publication failed: {failures}")
    return [item.event_id for item in entries if item.event_id is not None]
```

`PutEvents` reports per-entry failures in a successful HTTP response. Check
`failed_entry_count` and each entry's `error_code`.

Create a schedule using an existing target and execution role:

```python
from rboto import scheduler
from rboto_scheduler.types import Target

async def schedule_message(queue_arn: str, role_arn: str) -> str:
    target: Target = {
        "arn": queue_arn,
        "role_arn": role_arn,
        "input": '{"message":"scheduled by rboto"}',
    }
    response = await scheduler(region="us-east-1").create_schedule(
        name="daily-message",
        schedule_expression="cron(0 9 * * ? *)",
        schedule_expression_timezone="America/Sao_Paulo",
        flexible_time_window={"mode": "OFF"},
        target=target,
    )
    return response.schedule_arn
```

Create a pipe between existing SQS queues:

```python
from rboto import pipes

async def connect_queues(source_arn: str, target_arn: str, role_arn: str) -> str | None:
    response = await pipes(region="us-east-1").create_pipe(
        name="queue-forwarder",
        source=source_arn,
        target=target_arn,
        role_arn=role_arn,
        desired_state="RUNNING",
        source_parameters={"sqs_queue_parameters": {"batch_size": 1}},
    )
    return response.arn
```

The execution roles must trust their respective services and have access to their
sources and targets. Pipe creation is asynchronous; use `describe_pipe()` to check
`current_state`. Remove resources with `delete_schedule()` and `delete_pipe()` when done.

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
