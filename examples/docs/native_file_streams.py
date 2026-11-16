from pathlib import Path

from rboto import FileBody, S3Client


async def upload(client: S3Client, source: Path, bucket: str, key: str) -> None:
    await client.put_object(bucket=bucket, key=key, body=FileBody(source))


async def download(client: S3Client, bucket: str, key: str, destination: Path) -> int:
    response = await client.get_object(bucket=bucket, key=key)
    async with response.body:
        return await response.body.write_to(destination)
