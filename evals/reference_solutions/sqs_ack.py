from collections.abc import Awaitable, Callable

from rboto import SQSClient


async def run(
    endpoint_url: str,
    queue_url: str,
    handler: Callable[[str], Awaitable[None]],
) -> int:
    count = 0
    async with SQSClient(region="eu-west-2", endpoint_url=endpoint_url) as client:
        response = await client.receive_message(queue_url=queue_url, max_number_of_messages=10)
        for message in response.messages or []:
            if message.body is None or message.receipt_handle is None:
                continue
            await handler(message.body)
            await client.delete_message(
                queue_url=queue_url,
                receipt_handle=message.receipt_handle,
            )
            count += 1
    return count
