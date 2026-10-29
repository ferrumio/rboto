import asyncio

from rboto import DynamoDBClient, S3Client, configure_logging, operation_context

# One configuration for every client: JSON to stdout, no formatter required.
configure_logging(level="INFO", service_levels={"dynamodb": "DEBUG", "s3": "OFF"})


async def main() -> None:
    dynamo = DynamoDBClient()
    storage = S3Client()

    async def tables() -> None:
        with operation_context(correlation_id="list-tables"):
            await dynamo.list_tables()

    async def buckets() -> None:
        with operation_context(correlation_id="list-buckets"):
            await storage.list_buckets()

    async with asyncio.TaskGroup() as group:
        group.create_task(tables())
        group.create_task(buckets())


if __name__ == "__main__":
    asyncio.run(main())
