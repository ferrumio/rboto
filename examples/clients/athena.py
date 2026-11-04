import asyncio

from rboto import AthenaClient


async def main() -> None:
    client = AthenaClient(region="us-east-1")
    async for page in client.paginators.list_work_groups(max_results=10):
        for workgroup in page.work_groups or []:
            print(workgroup.name, workgroup.state)


if __name__ == "__main__":
    asyncio.run(main())
