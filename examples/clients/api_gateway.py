import asyncio

from rboto import APIGatewayClient


async def main() -> None:
    client = APIGatewayClient(region="us-east-1")
    response = await client.get_rest_apis(limit=10)
    for api in response.items or []:
        print(api.id, api.name)


if __name__ == "__main__":
    asyncio.run(main())
