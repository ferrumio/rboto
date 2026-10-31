from rboto import SNSClient
from rboto_sns.types import PublishOutput


async def publish(topic_arn: str) -> str | None:
    client = SNSClient(region="us-east-1")
    response: PublishOutput = await client.publish(
        topic_arn=topic_arn,
        message="hello",
    )
    return response.message_id
