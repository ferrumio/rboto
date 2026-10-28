import asyncio
from pathlib import Path

from rboto import S3Client


async def download(client: S3Client, bucket: str, key: str, destination: Path) -> None:
    response = await client.get_object(bucket=bucket, key=key)
    with destination.open("wb") as output:
        async for chunk in response.body:
            await asyncio.to_thread(output.write, chunk)
