from rboto import S3Client
from rboto_s3.types import CreateBucketOutput


async def create_bucket(client: S3Client, bucket: str) -> CreateBucketOutput:
    response = await client.create_bucket(bucket=bucket)
    return response
