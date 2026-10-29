import asyncio

from rboto import BedrockAgentCoreClient


async def main() -> None:
    client = BedrockAgentCoreClient(region="us-east-1")
    response = await client.list_actors(memory_id="support_memory-1234567890", max_results=10)
    for actor in response.actor_summaries:
        print(actor.actor_id)


if __name__ == "__main__":
    asyncio.run(main())
