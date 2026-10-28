import asyncio

from rboto import OperationEvent, S3Client, Session, operation_context


async def report(event: OperationEvent) -> None:
    if event.phase == "start":
        return
    print(
        event.context.operation,
        event.phase,
        event.duration,
        event.retry_count,
        event.request_id,
        event.context.correlation_id,
    )


async def main() -> None:
    session = Session(region="us-east-1", interceptors=(report,))
    client = S3Client(session=session)
    with operation_context(correlation_id="request-123"):
        await client.list_buckets()


if __name__ == "__main__":
    asyncio.run(main())
