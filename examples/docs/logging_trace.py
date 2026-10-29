import asyncio
from contextvars import ContextVar

from rboto import DynamoDBClient, LogTraceContext, configure_logging

trace_context: ContextVar[LogTraceContext | None] = ContextVar("application_trace", default=None)
configure_logging(trace_context=trace_context.get)


async def main() -> None:
    client = DynamoDBClient()
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
    asyncio.run(main())
