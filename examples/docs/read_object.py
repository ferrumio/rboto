from rboto import S3Client


async def read_object(client: S3Client, bucket: str, key: str) -> bytes:
    response = await client.get_object(bucket=bucket, key=key)
    return await response.body.read()
