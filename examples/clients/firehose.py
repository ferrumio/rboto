import asyncio
import os

from rboto import FirehoseClient


async def main() -> None:
    async with FirehoseClient(region="eu-west-2") as client:
        result = await client.put_record(
            delivery_stream_name=os.environ["FIREHOSE_DELIVERY_STREAM_NAME"],
            record={"data": b'{"order_id": "123", "status": "created"}\n'},
        )
        print(result.record_id)


if __name__ == "__main__":
    asyncio.run(main())
