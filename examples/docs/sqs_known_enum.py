from rboto import SQSClient
from rboto_sqs import inputs


async def copy_visibility_timeout(client: SQSClient, source: str, target: str) -> None:
    response = await client.get_queue_attributes(
        queue_url=source, attribute_names=["VisibilityTimeout"],
    )
    for key, value in (response.attributes or {}).items():
        if inputs.queue_attribute_name.is_known(key) and key == "VisibilityTimeout":
            await client.set_queue_attributes(queue_url=target, attributes={key: value})
