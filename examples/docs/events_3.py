from rboto import EventBridgePipesClient


async def connect_queues(source_arn: str, target_arn: str, role_arn: str) -> str | None:
    response = await EventBridgePipesClient(region="us-east-1").create_pipe(
        name="queue-forwarder",
        source=source_arn,
        target=target_arn,
        role_arn=role_arn,
        desired_state="RUNNING",
        source_parameters={"sqs_queue_parameters": {"batch_size": 1}},
    )
    return response.arn
