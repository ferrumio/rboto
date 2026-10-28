from rboto import EventBridgeSchedulerClient
from rboto_scheduler.types import Target


async def schedule_message(queue_arn: str, role_arn: str) -> str:
    target: Target = {
        "arn": queue_arn,
        "role_arn": role_arn,
        "input": '{"message":"scheduled by rboto"}',
    }
    response = await EventBridgeSchedulerClient(region="us-east-1").create_schedule(
        name="daily-message",
        schedule_expression="cron(0 9 * * ? *)",
        schedule_expression_timezone="America/Sao_Paulo",
        flexible_time_window={"mode": "OFF"},
        target=target,
    )
    return response.schedule_arn
