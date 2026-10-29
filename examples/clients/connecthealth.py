import asyncio

from rboto import ConnectHealthClient


async def main() -> None:
    client = ConnectHealthClient(region="us-east-1")
    response = await client.list_domains(max_results=10)
    for domain in response.domains:
        print(domain.domain_id)


if __name__ == "__main__":
    asyncio.run(main())
