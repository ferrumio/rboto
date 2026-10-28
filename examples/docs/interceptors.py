import asyncio
import logging
from time import perf_counter

from rboto import Next, OperationRequest, OperationResponse, S3Client, Session, operation_context

log = logging.getLogger(__name__)


async def policy(request: OperationRequest, call_next: Next) -> OperationResponse:
    if request.context.service == "s3" and request.context.operation == "DeleteBucket":
        raise PermissionError("Bucket deletion is disabled in this application")
    return await call_next(request)


async def timing(request: OperationRequest, call_next: Next) -> OperationResponse:
    started = perf_counter()
    try:
        return await call_next(request)
    finally:
        log.info(
            "%s.%s completed in %.3f s; request_id=%s; correlation_id=%s",
            request.context.service,
            request.context.operation,
            perf_counter() - started,
            request.metadata.request_id,
            request.context.correlation_id,
        )


async def main() -> None:
    session = Session(region="us-east-1", interceptors=(timing, policy))
    client = S3Client(session=session)
    with operation_context(correlation_id="request-123"):
        await client.list_buckets()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
