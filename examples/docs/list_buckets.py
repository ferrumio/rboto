from rboto import S3Client


async def list_buckets(client: S3Client) -> None:
    response = await client.list_buckets()
    for bucket in response.buckets or []:
        print(bucket.name)
