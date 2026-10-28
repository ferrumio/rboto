import asyncio
import json
import logging

from rboto import DynamoDBClient, LoggingInterceptor


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "rboto": record.__dict__.get("rboto"),
        }
        return json.dumps(document)


async def main() -> None:
    client = DynamoDBClient(interceptors=[LoggingInterceptor()])
    await client.list_tables()


if __name__ == "__main__":
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    asyncio.run(main())
