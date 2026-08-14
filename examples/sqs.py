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

from rboto import sqs


def configure_aws() -> str:
    return os.getenv("AWS_REGION", "us-east-1")


async def main() -> None:
    region = configure_aws()
    client = sqs(region=region)
    queue_name = f"rboto-example-{uuid.uuid4().hex[:12]}"
    queue_url: str | None = None

    try:
        created = await client.create_queue(
            queue_name=queue_name,
            tags={"example": "rboto"},
        )
        queue_url = created.get("queue_url")
        if queue_url is None:
            raise RuntimeError("CreateQueue did not return queue_url")
        print("created queue:", queue_url)

        sent = await client.send_message(
            queue_url=queue_url,
            message_body="hello from rboto",
            message_attributes={
                "source": {
                    "data_type": "String",
                    "string_value": "example",
                }
            },
        )
        print("sent message:", sent.get("message_id"))

        received = await client.receive_message(
            queue_url=queue_url,
            max_number_of_messages=1,
            wait_time_seconds=1,
            message_attribute_names=["All"],
        )
        messages = received.get("messages", [])
        for message in messages:
            print("received message:", message.get("body"))
            receipt_handle = message.get("receipt_handle")
            if receipt_handle is not None:
                await client.delete_message(
                    queue_url=queue_url,
                    receipt_handle=receipt_handle,
                )
                print("deleted message")

        attributes = await client.get_queue_attributes(
            queue_url=queue_url,
            attribute_names=["All"],
        )
        print("queue attributes:", attributes.get("attributes", {}))
    finally:
        if queue_url is not None:
            await client.delete_queue(queue_url=queue_url)
            print("deleted queue:", queue_url)


if __name__ == "__main__":
    asyncio.run(main())
