import asyncio

from rboto import CloudTrailClient


async def main() -> None:
    client = CloudTrailClient(region="us-east-1")
    response = await client.list_trails()
    for trail in response.trails or []:
        print(trail.name, trail.trail_arn)


if __name__ == "__main__":
    asyncio.run(main())
