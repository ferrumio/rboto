from rboto_sqs import SQSClient


async def receive(client: SQSClient, queue_url: str) -> None:
    response = await client.receive_message(
        queue_url=queue_url,
        max_number_of_messages=10,
        wait_time_seconds=10,
    )
    for message in response.messages or []:
        print(message.body)  # Replace with your application's processing.
        if message.receipt_handle is not None:
            await client.delete_message(queue_url=queue_url, receipt_handle=message.receipt_handle)
