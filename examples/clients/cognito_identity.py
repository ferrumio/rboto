import asyncio

from rboto import CognitoIdentityClient


async def main() -> None:
    client = CognitoIdentityClient(region="us-east-1")
    # The identity pool must allow unauthenticated identities for this request.
    response = await client.get_id(
        identity_pool_id="us-east-1:00000000-0000-0000-0000-000000000000",
    )
    print(response.identity_id)


if __name__ == "__main__":
    asyncio.run(main())
