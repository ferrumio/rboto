import asyncio

from rboto_sts import STSClient


async def identify(client: STSClient) -> str | None:
    async with asyncio.timeout(5):
        response = await client.get_caller_identity()
        return response.arn
