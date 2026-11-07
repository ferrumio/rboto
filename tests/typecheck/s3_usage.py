from collections.abc import AsyncIterator

from rboto import S3Client
from rboto_s3.types import GetObjectOutput, PutObjectOutput


async def download() -> bytes:
    client = S3Client(region="us-east-1")
    response: GetObjectOutput = await client.get_object(bucket="example", key="data.bin")
    chunks: list[bytes] = []
    async for chunk in response.body:
        chunks.append(chunk)
    return b"".join(chunks)


async def produce(payload: bytes) -> AsyncIterator[bytes]:
    for start in range(0, len(payload), 65_536):
        yield payload[start : start + 65_536]


async def upload(payload: bytes) -> None:
    client = S3Client(region="us-east-1")
    buffered: PutObjectOutput = await client.put_object(
        bucket="example", key="small.bin", body=payload
    )
    streamed: PutObjectOutput = await client.put_object(
        bucket="example",
        key="large.bin",
        body=produce(payload),
        content_length=len(payload),
    )
    assert buffered.e_tag is not None and streamed.e_tag is not None


async def copy_between_keys() -> None:
    client = S3Client(region="us-east-1")
    source = await client.get_object(bucket="example", key="data.bin")
    assert source.content_length is not None
    await client.put_object(
        bucket="example",
        key="copy.bin",
        body=source.body,
        content_length=source.content_length,
    )
