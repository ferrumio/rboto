from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from rboto import S3Client


class Bucket(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=True)

    name: str
    creation_date: datetime | None = Field(default=None, strict=False)


class BucketPage(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=True)

    buckets: list[Bucket] | None = None
    continuation_token: str | None = None


async def list_bucket_page(client: S3Client) -> BucketPage:
    response = await client.list_buckets()
    return BucketPage.model_validate(response)
