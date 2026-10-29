import asyncio

from rboto import BedrockAgentClient


async def main() -> None:
    client = BedrockAgentClient(region="us-east-1")
    response = await client.list_agents(max_results=10)
    for agent in response.agent_summaries:
        print(agent.agent_id, agent.agent_name)


if __name__ == "__main__":
    asyncio.run(main())
