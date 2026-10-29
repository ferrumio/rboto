import asyncio

from rboto import LambdaClient


async def main() -> None:
    client = LambdaClient(region="us-east-1")
    async for page in client.paginators.list_functions(max_items=50):
        for function in page.functions or []:
            print(function.function_name, function.runtime)


if __name__ == "__main__":
    asyncio.run(main())
