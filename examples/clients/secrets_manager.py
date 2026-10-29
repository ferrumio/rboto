import asyncio
import json

from rboto import SecretsManagerClient


async def main() -> None:
    client = SecretsManagerClient(region="us-east-1")
    response = await client.get_secret_value(secret_id="application/config")
    if response.secret_string is None:
        raise ValueError("Expected a text secret")
    # Pass the value to your application; do not print secret contents.
    settings = json.loads(response.secret_string)
    print("Loaded settings:", len(settings))


if __name__ == "__main__":
    asyncio.run(main())
