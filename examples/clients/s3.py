import asyncio

from rboto import S3Client
from rboto_s3.exceptions import NoSuchKeyError


async def main() -> None:
    client = S3Client(region="us-east-1")
    try:
        response = await client.get_object(bucket="my-bucket", key="hello.txt")
    except NoSuchKeyError:
        print("Object does not exist")
        return
    # Buffer this small text object. Use async iteration for large bodies.
    body: bytes = await response.body.read()
    print(body.decode("utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
