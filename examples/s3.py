import asyncio
import hashlib
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import cast

PROJECT_VENV = Path(__file__).resolve().parents[1] / ".venv"
if Path(sys.prefix).resolve() != PROJECT_VENV.resolve():
    venv_python = PROJECT_VENV / "bin" / "python"
    if not venv_python.exists():
        raise RuntimeError("project virtual environment not found: run python3 -m venv .venv")
    os.execv(venv_python, [str(venv_python), *sys.argv])

from rboto import s3
from rboto_s3 import ByteStream, S3Client
from rboto_s3.types import BucketLocationConstraint, CreateBucketConfiguration


@dataclass(frozen=True, slots=True)
class StreamResult:
    key: str
    chunks: int
    bytes_read: int
    sha256: str


def configure_aws() -> str:
    return os.getenv("AWS_REGION", "us-east-1")


def make_payload(index: int) -> bytes:
    block = f"stream-{index}:".encode() + bytes(range(256))
    return block * (4_096 + index * 1_024)


async def consume_stream(key: str, body: ByteStream) -> StreamResult:
    digest = hashlib.sha256()
    chunks = 0
    bytes_read = 0

    async for chunk in body:
        chunks += 1
        bytes_read += len(chunk)
        digest.update(chunk)

        # Yield control to demonstrate that other active streams can progress.
        await asyncio.sleep(0)

    return StreamResult(
        key=key,
        chunks=chunks,
        bytes_read=bytes_read,
        sha256=digest.hexdigest(),
    )


async def upload_objects(
    client: S3Client,
    bucket: str,
    payloads: dict[str, bytes],
) -> None:
    await asyncio.gather(
        *(
            client.put_object(
                bucket=bucket,
                key=key,
                body=payload,
                content_type="application/octet-stream",
                metadata={"expected-sha256": hashlib.sha256(payload).hexdigest()},
            )
            for key, payload in payloads.items()
        )
    )


async def download_concurrently(
    client: S3Client,
    bucket: str,
    payloads: dict[str, bytes],
) -> list[StreamResult]:
    # All responses remain alive at the same time, each owning an independent
    # Rust aws_smithy_types::ByteStream.
    responses = await asyncio.gather(
        *(client.get_object(bucket=bucket, key=key) for key in payloads)
    )

    results = await asyncio.gather(
        *(
            consume_stream(key, response["body"])
            for key, response in zip(payloads, responses, strict=True)
        )
    )

    for result in results:
        expected = payloads[result.key]
        expected_hash = hashlib.sha256(expected).hexdigest()
        if result.bytes_read != len(expected) or result.sha256 != expected_hash:
            raise RuntimeError(f"stream integrity check failed: {result.key}")

    return list(results)


async def main() -> None:
    region = configure_aws()
    client = s3(region=region)
    bucket = f"rboto-example-{uuid.uuid4().hex[:12]}"
    payloads = {
        f"stream-{index}.bin": make_payload(index)
        for index in range(1, 4)
    }
    bucket_created = False

    try:
        configuration: CreateBucketConfiguration | None = None
        if region != "us-east-1":
            configuration = {
                "location_constraint": cast(BucketLocationConstraint, region),
            }
        await client.create_bucket(
            bucket=bucket,
            create_bucket_configuration=configuration,
        )
        bucket_created = True
        print("created bucket:", bucket)

        await upload_objects(client, bucket, payloads)
        print("uploaded objects concurrently:", list(payloads))

        results = await download_concurrently(client, bucket, payloads)
        for result in results:
            print(
                "stream consumed:",
                result.key,
                f"chunks={result.chunks}",
                f"bytes={result.bytes_read}",
                f"sha256={result.sha256}",
            )

        # read() is the convenience path that aggregates the remaining stream.
        single = await client.get_object(bucket=bucket, key="stream-1.bin")
        aggregated = await single["body"].read()
        print("aggregated read bytes:", len(aggregated))

        listed = await client.list_objects_v2(bucket=bucket)
        for item in listed.get("contents", []):
            print("listed object:", item.get("key"), item.get("size"))
    finally:
        if bucket_created:
            await asyncio.gather(
                *(
                    client.delete_object(bucket=bucket, key=key)
                    for key in payloads
                )
            )
            if os.getenv("RBOTO_CLEANUP_BUCKET") == "1":
                await client.delete_bucket(bucket=bucket)
                print("deleted objects and bucket:", bucket)
            else:
                print("deleted objects; bucket kept:", bucket)


if __name__ == "__main__":
    asyncio.run(main())
