from rboto import sns
from rboto_sns import PublishResponse


async def publish(topic_arn: str) -> str | None:
    client = sns(region="us-east-1")
    response: PublishResponse = await client.publish(
        topic_arn=topic_arn,
        message="hello",
    )
    return response.message_id
