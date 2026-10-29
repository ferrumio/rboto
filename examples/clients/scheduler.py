import asyncio

from rboto import EventBridgeSchedulerClient


async def main() -> None:
    client = EventBridgeSchedulerClient(region="us-east-1")
    response = await client.get_schedule(name="nightly-report", group_name="default")
    print(response.schedule_expression, response.state)


if __name__ == "__main__":
    asyncio.run(main())
