from typing import assert_type

from rboto import DynamoDBClient, SQSClient, UnknownEnumValue
from rboto_bedrock_runtime.types import ToolUseBlockOutput
from rboto_sqs.types import GetQueueAttributesResult


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
