import asyncio

from rboto import QBusinessClient


async def main() -> None:
    client = QBusinessClient(region="us-east-1")
    response = await client.chat_sync(
        application_id="YOUR_APPLICATION_ID",
        user_id="YOUR_USER_ID",
        user_message="Where is our onboarding guide?",
    )
    print(response.system_message)


if __name__ == "__main__":
    asyncio.run(main())
