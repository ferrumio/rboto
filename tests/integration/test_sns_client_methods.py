import os
import uuid

import pytest
from rboto import sns


@pytest.mark.asyncio
async def test_sns_client_methods_reach_rust_and_localstack() -> None:
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url is None:
        pytest.skip("AWS_ENDPOINT_URL is required for integration tests")

    client = sns(region="us-east-1", endpoint_url=endpoint_url)
    created = await client.create_topic(
        name=f"rboto-client-methods-{uuid.uuid4().hex[:12]}",
        tags=[{"key": "suite", "value": "bridge"}],
    )
    assert created.topic_arn is not None

    try:
        attributes = await client.get_topic_attributes(topic_arn=created.topic_arn)
        assert attributes.attributes is not None
        assert attributes.attributes["TopicArn"] == created.topic_arn

        tags = await client.list_tags_for_resource(resource_arn=created.topic_arn)
        assert tags.tags is not None
        assert {(tag.key, tag.value) for tag in tags.tags} == {("suite", "bridge")}

        published = await client.publish(
            topic_arn=created.topic_arn,
            subject="rboto integration",
            message="hello from the Rust SDK",
        )
        assert published.message_id is not None

        topics = await client.list_topics()
        assert topics.topics is not None
        assert created.topic_arn in {topic.topic_arn for topic in topics.topics}
    finally:
        await client.delete_topic(topic_arn=created.topic_arn)
