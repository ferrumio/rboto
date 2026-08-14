import os
import uuid

import pytest
from rboto import sqs


@pytest.mark.asyncio
async def test_sqs_client_methods_reach_rust_and_localstack() -> None:
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url is None:
        pytest.skip("AWS_ENDPOINT_URL is required for integration tests")

    client = sqs(region="us-east-1", endpoint_url=endpoint_url)
    created = await client.create_queue(
        queue_name=f"rboto-native-output-{uuid.uuid4().hex[:12]}"
    )
    assert created.queue_url is not None

    try:
        resolved = await client.get_queue_url(queue_name=created.queue_url.rsplit("/", 1)[-1])
        assert resolved.queue_url == created.queue_url

        await client.tag_queue(queue_url=created.queue_url, tags={"suite": "bridge"})
        tags = await client.list_queue_tags(queue_url=created.queue_url)
        assert tags.tags == {"suite": "bridge"}

        sent = await client.send_message(
            queue_url=created.queue_url,
            message_body="typed output",
        )
        assert sent.message_id is not None

        batch = await client.send_message_batch(
            queue_url=created.queue_url,
            entries=[{"id": "batch-1", "message_body": "batched"}],
        )
        assert batch.successful is not None
        assert len(batch.successful) == 1

        received = await client.receive_message(
            queue_url=created.queue_url,
            max_number_of_messages=2,
            wait_time_seconds=1,
        )
        assert received.messages

        message = received.messages[0]
        assert message.body in {"typed output", "batched"}
        assert message.receipt_handle is not None
        await client.change_message_visibility(
            queue_url=created.queue_url,
            receipt_handle=message.receipt_handle,
            visibility_timeout=0,
        )
        await client.delete_message_batch(
            queue_url=created.queue_url,
            entries=[{"id": "received-1", "receipt_handle": message.receipt_handle}],
        )

        queues = await client.list_queues(queue_name_prefix="rboto-native-output-")
        assert queues.queue_urls is not None
        assert created.queue_url in queues.queue_urls
    finally:
        await client.delete_queue(queue_url=created.queue_url)
