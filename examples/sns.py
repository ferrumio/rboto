import asyncio
import os
import sys
import uuid
from pathlib import Path

PROJECT_VENV = Path(__file__).resolve().parents[1] / ".venv"
if Path(sys.prefix).resolve() != PROJECT_VENV.resolve():
    venv_python = PROJECT_VENV / "bin" / "python"
    if not venv_python.exists():
        raise RuntimeError("project virtual environment not found: run python3 -m venv .venv")
    os.execv(venv_python, [str(venv_python), *sys.argv])

from rboto import sns


def configure_aws() -> str:
    return os.getenv("AWS_REGION", "us-east-1")


async def main() -> None:
    client = sns(
        region=configure_aws(),
        endpoint_url=os.getenv("AWS_ENDPOINT_URL"),
    )
    topic_arn: str | None = None

    try:
        created = await client.create_topic(
            name=f"rboto-example-{uuid.uuid4().hex[:12]}",
            tags=[{"key": "example", "value": "rboto"}],
        )
        topic_arn = created.topic_arn
        if topic_arn is None:
            raise RuntimeError("CreateTopic did not return topic_arn")
        print("created topic:", topic_arn)

        published = await client.publish(
            topic_arn=topic_arn,
            subject="rboto example",
            message="hello from rboto",
        )
        print("published message:", published.message_id)

        attributes = await client.get_topic_attributes(topic_arn=topic_arn)
        print("topic display name:", (attributes.attributes or {}).get("DisplayName"))

        tags = await client.list_tags_for_resource(resource_arn=topic_arn)
        for tag in tags.tags or []:
            print("topic tag:", tag.key, tag.value)
    finally:
        if topic_arn is not None:
            await client.delete_topic(topic_arn=topic_arn)
            print("deleted topic:", topic_arn)


if __name__ == "__main__":
    asyncio.run(main())
