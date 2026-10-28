import asyncio

from rboto import STSClient


async def main() -> None:
    client = STSClient(region="us-east-1")
    response = await client.get_caller_identity()
    print(response.account)
    print(response.arn)


if __name__ == "__main__":
    asyncio.run(main())
