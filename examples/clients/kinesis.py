import asyncio
import os

from rboto import KinesisClient


async def main() -> None:
    async with KinesisClient(region="eu-west-2") as client:
        result = await client.put_record(
            stream_name=os.environ["KINESIS_STREAM_NAME"],
            partition_key="order-123",
            data=b'{"order_id": "123", "status": "created"}',
        )
        print(result.shard_id, result.sequence_number)


if __name__ == "__main__":
    asyncio.run(main())
