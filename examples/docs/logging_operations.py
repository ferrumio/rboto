import asyncio
import logging

from rboto import DynamoDBClient, LoggingInterceptor, S3Client, Session, operation_context


async def main() -> None:
    session = Session(interceptors=(LoggingInterceptor(),))
    dynamo = DynamoDBClient(session=session)
    storage = S3Client(session=session)

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
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(levelname)s %(name)s %(message)s %(rboto)s",
            defaults={"rboto": {}},
        )
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("rboto.dynamodb").setLevel(logging.DEBUG)
    logging.getLogger("rboto.s3").setLevel(logging.WARNING)
    asyncio.run(main())
