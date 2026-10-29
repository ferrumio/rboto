import asyncio

from rboto import SQSClient


async def main() -> None:
    client = SQSClient(region="us-east-1")
    response = await client.send_message(
        queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/orders",
        message_body='{"order_id":"123"}',
    )
    print(response.message_id)


if __name__ == "__main__":
    asyncio.run(main())
