from rboto import SNSClient


async def publish(client: SNSClient, topic_arn: str, message: str) -> str | None:
    response = await client.publish(topic_arn=topic_arn, message=message)
    return response.message_id
