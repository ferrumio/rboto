import asyncio
import os

from rboto import GlueClient


async def main() -> None:
    async with GlueClient(region=os.environ.get("AWS_REGION", "us-east-1")) as client:
        async for page in client.paginators.get_databases(max_results=10):
            for database in page.database_list or []:
                print(database.name, database.description)


if __name__ == "__main__":
    asyncio.run(main())
