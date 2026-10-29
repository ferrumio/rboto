import asyncio

from rboto import LambdaClient


async def main() -> None:
    client = LambdaClient(region="us-east-1")
    response = await client.invoke(function_name="my-function", payload=b'{"name":"Ada"}')
    if response.function_error:
        raise RuntimeError(response.function_error)
    print(response.status_code)
    print(response.payload)


if __name__ == "__main__":
    asyncio.run(main())
