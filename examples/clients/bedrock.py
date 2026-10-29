import asyncio

from rboto import BedrockClient


async def main() -> None:
    client = BedrockClient(region="us-east-1")
    response = await client.list_foundation_models(by_output_modality="TEXT")
    for model in response.model_summaries or []:
        print(model.model_id, model.model_name)


if __name__ == "__main__":
    asyncio.run(main())
