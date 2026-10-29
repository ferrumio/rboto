import asyncio

from rboto import BedrockAgentCoreControlClient


async def main() -> None:
    client = BedrockAgentCoreControlClient(region="us-east-1")
    response = await client.list_agent_runtimes(max_results=10)
    for runtime in response.agent_runtimes:
        print(runtime.agent_runtime_id, runtime.agent_runtime_name)


if __name__ == "__main__":
    asyncio.run(main())
