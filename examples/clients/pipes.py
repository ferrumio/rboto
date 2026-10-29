import asyncio

from rboto import EventBridgePipesClient


async def main() -> None:
    client = EventBridgePipesClient(region="us-east-1")
    response = await client.list_pipes(name_prefix="orders", limit=10)
    for pipe in response.pipes or []:
        print(pipe.name, pipe.current_state)


if __name__ == "__main__":
    asyncio.run(main())
