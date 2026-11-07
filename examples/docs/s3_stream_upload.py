import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from rboto import S3Client


async def read_file(path: Path, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while chunk := await asyncio.to_thread(handle.read, chunk_size):
            yield chunk


async def upload_file(client: S3Client, bucket: str, key: str, path: Path) -> None:
    await client.put_object(
        bucket=bucket,
        key=key,
        body=read_file(path),
        content_length=path.stat().st_size,
    )
