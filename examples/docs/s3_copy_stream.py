from rboto import S3Client


async def copy_object(client: S3Client, source: str, destination: str, key: str) -> None:
    response = await client.get_object(bucket=source, key=key)
    assert response.content_length is not None
    await client.put_object(
        bucket=destination,
        key=key,
        body=response.body,
        content_length=response.content_length,
    )
