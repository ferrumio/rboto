from typing import assert_type

from rboto import BedrockRuntimeClient, DynamoDBClient, SQSClient, UnknownEnumValue
from rboto_bedrock_runtime import inputs as bedrock_inputs
from rboto_bedrock_runtime.types import Message, ToolUseBlockOutput
from rboto_dynamodb import inputs as dynamodb_inputs
from rboto_sqs import GetQueueAttributesResult
from rboto_sqs import inputs as sqs_inputs
from rboto_sqs.types import QueueAttributeName


def read_tool_kind(block: ToolUseBlockOutput) -> str:
    kind = block["type"]
    return kind.upper() if kind is not None else "unspecified"


def queue_attributes(response: GetQueueAttributesResult) -> str | None:
    attributes = response.attributes
    if attributes is None:
        return None
    assert_type(attributes["VisibilityTimeout"], str)
    return attributes.get(UnknownEnumValue("FutureAttribute"))


async def sequence_inputs(sqs: SQSClient, dynamodb: DynamoDBClient, url: str) -> None:
    await sqs.receive_message(
        queue_url=url,
        message_system_attribute_names=("SentTimestamp", "SenderId"),
    )
    await dynamodb.put_item(
        table_name="Example",
        item={"pk": {"s": "example"}, "tags": {"ss": ("a", "b")}},
    )


async def reuse_responses(
    dynamodb: DynamoDBClient, bedrock: BedrockRuntimeClient, sqs: SQSClient, url: str,
) -> None:
    page = await dynamodb.query(table_name="Example")
    if page.last_evaluated_key:
        await dynamodb.query(
            table_name="Example", exclusive_start_key=dynamodb_inputs.key(page.last_evaluated_key),
        )

    messages: list[Message] = [{"role": "user", "content": [{"text": "hello"}]}]
    response = await bedrock.converse(model_id="example", messages=messages)
    output = response.output
    if output is not None and "unknown" not in output:
        messages.append(bedrock_inputs.message(output["message"]))
        await bedrock.converse(model_id="example", messages=messages)

    attributes = (await sqs.get_queue_attributes(queue_url=url)).attributes
    if attributes is not None:
        for key, value in attributes.items():
            if sqs_inputs.queue_attribute_name.is_known(key):
                assert_type(key, QueueAttributeName)
                await sqs.set_queue_attributes(queue_url=url, attributes={key: value})
        await sqs.set_queue_attributes(
            queue_url=url, attributes=sqs_inputs.queue_attribute_map(attributes),
        )
