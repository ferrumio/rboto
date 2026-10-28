import time

from rboto import cloudwatch_logs
from rboto_cloudwatch_logs.types import InputLogEvent


async def write_log(group: str, stream: str, message: str) -> None:
    event: InputLogEvent = {"timestamp": int(time.time() * 1000), "message": message}
    response = await cloudwatch_logs(region="us-east-1").put_log_events(
        log_group_name=group,
        log_stream_name=stream,
        log_events=[event],
    )
    if response.rejected_log_events_info is not None:
        raise RuntimeError("One or more log events were rejected")
