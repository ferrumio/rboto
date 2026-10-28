from rboto import CloudWatchLogsClient


async def tail_logs(client: CloudWatchLogsClient, group_arn: str) -> None:
    response = await client.start_live_tail(log_group_identifiers=[group_arn])
    async for event in response["response_stream"]:
        print(event)
