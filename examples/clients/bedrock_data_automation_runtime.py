import asyncio

from rboto import BedrockDataAutomationRuntimeClient


async def main() -> None:
    client = BedrockDataAutomationRuntimeClient(region="us-east-1")
    response = await client.get_data_automation_status(
        invocation_arn="arn:aws:bedrock:us-east-1:123456789012:data-automation-invocation/example",
    )
    print(response.status, response.output_configuration)


if __name__ == "__main__":
    asyncio.run(main())
