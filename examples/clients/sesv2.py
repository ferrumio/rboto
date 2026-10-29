import asyncio

from rboto import SESV2Client
from rboto_sesv2.types import EmailContent


async def main() -> None:
    client = SESV2Client(region="us-east-1")
    content: EmailContent = {
        "simple": {
            "subject": {"data": "Hello from rboto"},
            "body": {"text": {"data": "Your report is ready."}},
        },
    }
    # Use verified identities; sandbox accounts also require verified recipients.
    response = await client.send_email(
        from_email_address="sender@example.com",
        destination={"to_addresses": ["recipient@example.com"]},
        content=content,
    )
    print(response.message_id)


if __name__ == "__main__":
    asyncio.run(main())
