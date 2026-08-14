from rboto import s3
from rboto_s3 import GetObjectOutput


async def download() -> bytes:
    client = s3(region="us-east-1")
    response: GetObjectOutput = await client.get_object(bucket="example", key="data.bin")
    chunks: list[bytes] = []
    async for chunk in response["body"]:
        chunks.append(chunk)
    return b"".join(chunks)
