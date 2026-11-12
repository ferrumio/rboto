import hashlib

from rboto import S3Client
from rboto_s3.exceptions import NoSuchKeyError


async def run(endpoint_url: str, bucket: str, key: str) -> str | None:
    async with S3Client(region="eu-west-2", endpoint_url=endpoint_url) as client:
        try:
            response = await client.get_object(bucket=bucket, key=key)
        except NoSuchKeyError:
            return None
        digest = hashlib.sha256()
        async with response.body as body:
            async for chunk in body:
                digest.update(chunk)
        return digest.hexdigest()
