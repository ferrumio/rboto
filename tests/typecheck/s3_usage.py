from rboto import S3Client
from rboto_s3 import GetObjectOutput


async def download() -> bytes:
    client = S3Client(region="us-east-1")
    response: GetObjectOutput = await client.get_object(bucket="example", key="data.bin")
    chunks: list[bytes] = []
    async for chunk in response.body:
        chunks.append(chunk)
    return b"".join(chunks)
