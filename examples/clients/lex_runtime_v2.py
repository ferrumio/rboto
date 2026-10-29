import asyncio

from rboto import LexRuntimeV2Client


async def main() -> None:
    client = LexRuntimeV2Client(region="us-east-1")
    response = await client.recognize_text(
        bot_id="YOUR_BOT_ID",
        bot_alias_id="YOUR_ALIAS_ID",
        locale_id="en_US",
        session_id="example-session",
        text="I need help with my order",
    )
    print(response.messages)


if __name__ == "__main__":
    asyncio.run(main())
