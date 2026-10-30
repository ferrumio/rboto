from rboto import DynamoDBClient, SQSClient
from rboto_dynamodb.types import AttributeValue, GetItemOutput
from rboto_sqs.types import SendMessageResult


async def send(queue_url: str) -> str | None:
    client = SQSClient(region="us-east-1")
    response: SendMessageResult = await client.send_message(
        queue_url=queue_url,
        message_body="hello",
    )
    return response.message_id


async def get(table_name: str) -> GetItemOutput:
    client = DynamoDBClient(region="us-east-1")
    key: dict[str, AttributeValue] = {"pk": {"s": "USER#1"}}
    return await client.get_item(table_name=table_name, key=key)
