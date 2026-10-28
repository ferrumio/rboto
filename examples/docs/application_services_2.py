from collections.abc import AsyncIterator

from rboto import cloudwatch_logs
from rboto_cloudwatch_logs.types import StartLiveTailResponseStream


async def tail_logs(group_arn: str) -> AsyncIterator[StartLiveTailResponseStream]:
    response = await cloudwatch_logs(region="us-east-1").start_live_tail(
        log_group_identifiers=[group_arn],
    )
    async for event in response["response_stream"]:
        yield event
