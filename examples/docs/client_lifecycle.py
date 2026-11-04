from rboto import S3Client


async def first_chunk(bucket: str, key: str) -> bytes:
    async with S3Client(region="eu-west-2") as client:
        response = await client.get_object(bucket=bucket, key=key)
        async with response.body as body:
            return await anext(body, b"")
