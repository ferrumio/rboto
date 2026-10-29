import asyncio

from rboto import SNSClient


async def main() -> None:
    client = SNSClient(region="us-east-1")
    response = await client.publish(
        topic_arn="arn:aws:sns:us-east-1:123456789012:notifications",
        message="Your report is ready",
    )
    print(response.message_id)


if __name__ == "__main__":
    asyncio.run(main())
