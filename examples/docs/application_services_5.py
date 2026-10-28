import json

from rboto import StepFunctionsClient


async def start_workflow(state_machine_arn: str, order_id: str) -> str:
    response = await StepFunctionsClient(region="us-east-1").start_execution(
        state_machine_arn=state_machine_arn,
        input=json.dumps({"order_id": order_id}),
    )
    return response.execution_arn
