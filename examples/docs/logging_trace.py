import asyncio
import logging
from contextvars import ContextVar

from rboto import DynamoDBClient, LoggingInterceptor, LogTraceContext

trace_context: ContextVar[LogTraceContext | None] = ContextVar("application_trace", default=None)


async def main() -> None:
    client = DynamoDBClient(interceptors=[LoggingInterceptor(trace_context=trace_context.get)])
    # In an application, obtain these IDs from the tracer's current span.
    token = trace_context.set(
        LogTraceContext(
            trace_id="0123456789abcdef0123456789abcdef",
            span_id="0123456789abcdef",
        )
    )
    try:
        await client.list_tables()
    finally:
        trace_context.reset(token)


if __name__ == "__main__":
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s %(rboto)s", defaults={"rboto": {}}))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    asyncio.run(main())
