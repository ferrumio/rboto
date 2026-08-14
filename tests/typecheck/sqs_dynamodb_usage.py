from rboto import dynamodb, sqs
from rboto_dynamodb import GetItemOutput
from rboto_dynamodb.types import AttributeValue
from rboto_sqs import SendMessageResult


async def send(queue_url: str) -> str | None:
    client = sqs(region="us-east-1")
    response: SendMessageResult = await client.send_message(
        queue_url=queue_url,
        message_body="hello",
    )
    return response.message_id


async def get(table_name: str) -> GetItemOutput:
    client = dynamodb(region="us-east-1")
    key: dict[str, AttributeValue] = {"pk": {"s": "USER#1"}}
    return await client.get_item(table_name=table_name, key=key)
