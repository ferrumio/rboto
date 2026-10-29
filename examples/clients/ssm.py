import asyncio

from rboto import SSMClient


async def main() -> None:
    client = SSMClient(region="us-east-1")
    response = await client.get_parameter(name="/application/region")
    if response.parameter is not None:
        print(response.parameter.value)


if __name__ == "__main__":
    asyncio.run(main())
