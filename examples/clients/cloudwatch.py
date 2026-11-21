import asyncio

from rboto import CloudWatchClient


async def main() -> None:
    async with CloudWatchClient(region="eu-west-2") as client:
        await client.put_metric_data(
            namespace="MyApplication",
            metric_data=[{
                "metric_name": "ProcessedEvents",
                "value": 1.0,
                "unit": "Count",
                "dimensions": [{"name": "Environment", "value": "development"}],
            }],
        )
        async for page in client.paginators.describe_alarms(max_records=20):
            for alarm in page.metric_alarms or []:
                print(alarm.alarm_name, alarm.state_value)


if __name__ == "__main__":
    asyncio.run(main())
