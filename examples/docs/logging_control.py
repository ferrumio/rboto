import asyncio
import logging

from rboto import S3Client, configure_logging, get_logger

client = S3Client()
configure_logging()  # Also applies to clients created before this call.


async def main() -> None:
    logger = get_logger("s3")
    assert logger.name == client.logger_name == "rboto.s3"

    await client.list_buckets()
    logger.disabled = True
    await client.list_buckets()
    logger.disabled = False
    logger.setLevel(logging.DEBUG)
    await client.list_buckets()


if __name__ == "__main__":
    asyncio.run(main())
