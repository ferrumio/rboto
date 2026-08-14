import asyncio
import os
import uuid

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
