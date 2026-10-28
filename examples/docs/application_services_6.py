from rboto import SESV2Client
from rboto_sesv2.types import EmailContent


async def send_message(sender: str, recipient: str, text: str) -> str | None:
    content: EmailContent = {
        "simple": {
            "subject": {"data": "Notification", "charset": "UTF-8"},
            "body": {"text": {"data": text, "charset": "UTF-8"}},
        }
    }
    response = await SESV2Client(region="us-east-1").send_email(
        from_email_address=sender,
        destination={"to_addresses": [recipient]},
        content=content,
    )
    return response.message_id
