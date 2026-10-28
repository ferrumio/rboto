from rboto import DynamoDBClient
from rboto_dynamodb.types import AttributeValue


async def put_and_get(client: DynamoDBClient, table: str) -> None:
    item: dict[str, AttributeValue] = {
        "pk": {"s": "USER#1"},
        "name": {"s": "Ada"},
        "age": {"n": "37"},
        "active": {"bool": True},
    }
    await client.put_item(table_name=table, item=item)
    response = await client.get_item(
        table_name=table,
        key={"pk": {"s": "USER#1"}},
        consistent_read=True,
    )
    print(response.item)
