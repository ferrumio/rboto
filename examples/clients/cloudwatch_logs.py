import asyncio

from rboto import CloudWatchLogsClient


async def main() -> None:
    client = CloudWatchLogsClient(region="us-east-1")
    response = await client.describe_log_groups(log_group_name_prefix="/aws/lambda/", limit=10)
    for group in response.log_groups or []:
        print(group.log_group_name)


if __name__ == "__main__":
    asyncio.run(main())
