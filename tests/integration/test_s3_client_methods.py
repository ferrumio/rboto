import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from rboto import ClientConfig, s3
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

    finally:
        await asyncio.gather(*(client.delete_object(bucket=bucket, key=key) for key in keys))
        await client.delete_bucket(bucket=bucket)
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_chunk", [False, True], ids=["producer-error", "invalid-chunk"])
async def test_streaming_body_failure_preserves_error_and_stops_producer(
    invalid_chunk: bool,
) -> None:
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url is None:
        pytest.skip("AWS_ENDPOINT_URL is required for integration tests")

    error = RuntimeError("producer failed")
    cause = LookupError("producer cause")
    pulled = 0
    loop = asyncio.get_running_loop()

    async def source() -> AsyncIterator[bytes]:
        nonlocal pulled
        assert asyncio.get_running_loop() is loop
        pulled += 1
        yield b"partial"
        pulled += 1
        if invalid_chunk:
            yield "not bytes"  # type: ignore[misc]
            pytest.fail("producer was polled after a bad chunk")
        raise error from cause

    async with s3(
        region="us-east-1", endpoint_url=endpoint_url,
        config=ClientConfig(max_attempts=3),
    ) as client:
        bucket = f"rboto-failed-upload-{uuid.uuid4().hex[:12]}"
        await client.create_bucket(bucket=bucket)
        chunks = source()
        try:
            with pytest.raises(TypeError if invalid_chunk else RuntimeError) as caught:
                await asyncio.wait_for(
                    client.put_object(
                        bucket=bucket, key="failed.bin", body=chunks, content_length=1_024,
                    ),
                    timeout=10,
                )
            if invalid_chunk:
                assert "chunks must be bytes" in str(caught.value)
            else:
                assert caught.value is error
                assert caught.value.__cause__ is cause
            assert pulled == 2
            objects = await client.list_objects_v2(bucket=bucket)
            assert not objects.contents
        finally:
            await chunks.aclose()
            await client.delete_object(bucket=bucket, key="failed.bin")
            await client.delete_bucket(bucket=bucket)


@pytest.mark.asyncio
@pytest.mark.parametrize("close_client", [False, True], ids=["cancel-request", "close-client"])
async def test_streaming_body_cancels_pending_python_poll(close_client: bool) -> None:
    from rboto.exceptions import ClientClosedError

    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url is None:
        pytest.skip("AWS_ENDPOINT_URL is required for integration tests")

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def source() -> AsyncIterator[bytes]:
        try:
            yield b"partial"
            entered.set()
            await asyncio.Event().wait()
            pytest.fail("producer resumed after cancellation")
        finally:
            cancelled.set()

    async with s3(region="us-east-1", endpoint_url=endpoint_url) as admin:
        bucket = f"rboto-cancelled-upload-{uuid.uuid4().hex[:12]}"
        await admin.create_bucket(bucket=bucket)
        client = s3(region="us-east-1", endpoint_url=endpoint_url)
        chunks = source()
        upload = asyncio.create_task(client.put_object(
            bucket=bucket, key="cancelled.bin", body=chunks, content_length=1_024,
        ))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            if close_client:
                await asyncio.wait_for(client.aclose(), timeout=5)
            else:
                upload.cancel()
            with pytest.raises(ClientClosedError if close_client else asyncio.CancelledError):
                await asyncio.wait_for(upload, timeout=5)
            await asyncio.wait_for(cancelled.wait(), timeout=5)
        finally:
            upload.cancel()
            await asyncio.gather(upload, return_exceptions=True)
            await client.aclose()
            await chunks.aclose()
            await admin.delete_object(bucket=bucket, key="cancelled.bin")
            await admin.delete_bucket(bucket=bucket)
