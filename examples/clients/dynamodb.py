import asyncio

from rboto import DynamoDBClient
from rboto_dynamodb.types import AttributeValue


async def main() -> None:
    client = DynamoDBClient(region="us-east-1")
    key: dict[str, AttributeValue] = {"pk": {"s": "USER#123"}}
    response = await client.get_item(table_name="users", key=key)
    print(response.item)


if __name__ == "__main__":
    asyncio.run(main())
