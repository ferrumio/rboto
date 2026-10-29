import asyncio

from rboto import GuardDutyClient


async def main() -> None:
    client = GuardDutyClient(region="us-east-1")
    response = await client.list_detectors(max_results=10)
    print(response.detector_ids)


if __name__ == "__main__":
    asyncio.run(main())
