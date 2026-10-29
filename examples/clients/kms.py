import asyncio

from rboto import KMSClient


async def main() -> None:
    client = KMSClient(region="us-east-1")
    response = await client.encrypt(key_id="alias/application", plaintext=b"hello")
    print(response.ciphertext_blob)


if __name__ == "__main__":
    asyncio.run(main())
