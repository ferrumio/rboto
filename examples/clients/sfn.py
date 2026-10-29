import asyncio

from rboto import StepFunctionsClient


async def main() -> None:
    client = StepFunctionsClient(region="us-east-1")
    response = await client.start_execution(
        state_machine_arn="arn:aws:states:us-east-1:123456789012:stateMachine:example",
        input='{"order_id":"123"}',
    )
    print(response.execution_arn)


if __name__ == "__main__":
    asyncio.run(main())
