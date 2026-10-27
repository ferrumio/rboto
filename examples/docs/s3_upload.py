from rboto_s3 import S3Client


async def upload(client: S3Client, bucket: str, key: str) -> None:
    await client.put_object(
        bucket=bucket,
        key=key,
        body=b"hello from rboto",
        content_type="text/plain",
    )
