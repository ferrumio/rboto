import trio
from rboto import S3Client


async def main() -> None:
    async with S3Client(region="eu-west-2") as client:
        response = await client.list_buckets()
        for bucket in response.buckets or []:
            print(bucket.name)


if __name__ == "__main__":
    trio.run(main)
