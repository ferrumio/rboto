import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from rboto import s3
from rboto_s3.exceptions import NoSuchKeyError


@pytest.mark.asyncio
async def test_s3_client_methods_reach_rust_and_localstack() -> None:
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url is None:
        pytest.skip("AWS_ENDPOINT_URL is required for integration tests")

    client = s3(region="us-east-1", endpoint_url=endpoint_url)
    bucket = f"rboto-native-output-{uuid.uuid4().hex[:12]}"
    payloads = {"first.bin": b"first" * 16_384, "second.bin": b"second" * 12_288}
    await client.create_bucket(bucket=bucket)

    try:
        await client.head_bucket(bucket=bucket)
        await client.put_object(
            bucket=bucket,
            key="first.bin",
            body=payloads["first.bin"],
        )
        await client.put_object(
            bucket=bucket,
            key="second.bin",
            body=payloads["second.bin"],
        )
        responses = await asyncio.gather(
            *(client.get_object(bucket=bucket, key=key) for key in payloads)
        )
        bodies = await asyncio.gather(*(response.body.read() for response in responses))
        assert bodies == list(payloads.values())

        headed = await client.head_object(bucket=bucket, key="first.bin")
        assert headed.content_length == len(payloads["first.bin"])

        await client.copy_object(
            bucket=bucket,
            copy_source=f"{bucket}/first.bin",
            key="copy.bin",
        )
        copied = await client.get_object(bucket=bucket, key="copy.bin")
        assert await copied.body.read() == payloads["first.bin"]

        multipart = await client.create_multipart_upload(
            bucket=bucket,
            key="multipart.bin",
        )
        assert multipart.upload_id is not None
        part = await client.upload_part(
            bucket=bucket,
            key="multipart.bin",
            part_number=1,
            upload_id=multipart.upload_id,
            body=b"multipart payload",
        )
        assert part.e_tag is not None
        await client.complete_multipart_upload(
            bucket=bucket,
            key="multipart.bin",
            upload_id=multipart.upload_id,
            multipart_upload={
                "parts": [{"e_tag": part.e_tag, "part_number": 1}],
            },
        )
        completed = await client.get_object(bucket=bucket, key="multipart.bin")
        assert await completed.body.read() == b"multipart payload"

        listed = await client.list_objects_v2(bucket=bucket)
        assert listed.contents is not None
        assert {item.key for item in listed.contents} == {
            *payloads,
            "copy.bin",
            "multipart.bin",
        }

        with pytest.raises(NoSuchKeyError):
            await client.get_object(bucket=bucket, key="missing.bin")
    finally:
        await asyncio.gather(
            *(
                client.delete_object(bucket=bucket, key=key)
                for key in (*payloads, "copy.bin", "multipart.bin")
            )
        )
        await client.delete_bucket(bucket=bucket)


async def _chunks(payload: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(payload), size):
        await asyncio.sleep(0)
        yield payload[start : start + size]


async def _failing_chunks() -> AsyncIterator[bytes]:
    yield b"partial"
    raise RuntimeError("producer failed")


@pytest.mark.asyncio
async def test_s3_streams_request_bodies_from_async_iterables() -> None:
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url is None:
        pytest.skip("AWS_ENDPOINT_URL is required for integration tests")

    client = s3(region="us-east-1", endpoint_url=endpoint_url)
    bucket = f"rboto-streaming-upload-{uuid.uuid4().hex[:12]}"
    payload = bytes(range(256)) * 2_048
    keys = ("streamed.bin", "chained.bin", "multipart.bin")
    await client.create_bucket(bucket=bucket)

    try:
        await client.put_object(
            bucket=bucket,
            key="streamed.bin",
            body=_chunks(payload, 4_096),
            content_length=len(payload),
        )
        streamed = await client.get_object(bucket=bucket, key="streamed.bin")
        assert await streamed.body.read() == payload

        # A response ByteStream is itself an async iterator and can feed another request.
        source = await client.get_object(bucket=bucket, key="streamed.bin")
        await client.put_object(
            bucket=bucket,
            key="chained.bin",
            body=source.body,
            content_length=len(payload),
        )
        chained = await client.get_object(bucket=bucket, key="chained.bin")
        assert await chained.body.read() == payload

        multipart = await client.create_multipart_upload(bucket=bucket, key="multipart.bin")
        assert multipart.upload_id is not None
        part_payload = b"p" * (5 * 1024 * 1024)
        part = await client.upload_part(
            bucket=bucket,
            key="multipart.bin",
            part_number=1,
            upload_id=multipart.upload_id,
            body=_chunks(part_payload, 65_536),
            content_length=len(part_payload),
        )
        assert part.e_tag is not None
        await client.complete_multipart_upload(
            bucket=bucket,
            key="multipart.bin",
            upload_id=multipart.upload_id,
            multipart_upload={"parts": [{"e_tag": part.e_tag, "part_number": 1}]},
        )
        completed = await client.head_object(bucket=bucket, key="multipart.bin")
        assert completed.content_length == len(part_payload)

        with pytest.raises(ValueError, match="content_length is required"):
            await client.put_object(bucket=bucket, key="unsized.bin", body=_chunks(payload, 4_096))

        with pytest.raises(RuntimeError, match="producer failed"):
            await client.put_object(
                bucket=bucket,
                key="failed.bin",
                body=_failing_chunks(),
                content_length=1_024,
            )
    finally:
        await asyncio.gather(*(client.delete_object(bucket=bucket, key=key) for key in keys))
        await client.delete_bucket(bucket=bucket)
