from pydantic import BaseModel, ConfigDict
from rboto import S3Client


class BucketSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str


async def run(endpoint_url: str) -> list[BucketSummary]:
    async with S3Client(region="eu-west-2", endpoint_url=endpoint_url) as client:
        response = await client.list_buckets()
        return [
            BucketSummary.model_validate(bucket)
            for bucket in response.buckets or []
            if bucket.name is not None
        ]
