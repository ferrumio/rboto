import asyncio

from rboto import SignInClient


async def main() -> None:
    client = SignInClient(region="us-east-1")
    response = await client.get_console_authorization_configuration()
    print(response.target_id, response.console_authorization_enabled)


if __name__ == "__main__":
    asyncio.run(main())
