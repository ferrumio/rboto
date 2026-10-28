from rboto import SQSClient
from rboto_sqs.types import MessageAttributeValue


async def send_message(client: SQSClient, queue_url: str) -> str | None:
    source: MessageAttributeValue = {"data_type": "String", "string_value": "orders"}
    response = await client.send_message(
        queue_url=queue_url,
        message_body="Order received",
        message_attributes={"source": source},
    )
    return response.message_id
