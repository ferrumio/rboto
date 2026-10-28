from collections.abc import AsyncIterator

from rboto_s3 import S3Client


async def list_keys(client: S3Client, bucket: str) -> AsyncIterator[str]:
    token: str | None = None
    while True:
        response = await client.list_objects_v2(bucket=bucket, continuation_token=token)
        for entry in response.contents or []:
            if entry.key is not None:
                yield entry.key
        token = response.next_continuation_token
        if not token:
            break
