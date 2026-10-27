from collections.abc import AsyncIterator

from rboto_dynamodb import DynamoDBClient
from rboto_dynamodb.types import AttributeValue


async def query_items(
    client: DynamoDBClient,
    table: str,
    partition: str,
) -> AsyncIterator[dict[str, AttributeValue]]:
    key: dict[str, AttributeValue] | None = None
    while True:
        response = await client.query(
            table_name=table,
            key_condition_expression="pk = :pk",
            expression_attribute_values={":pk": {"s": partition}},
            exclusive_start_key=key,
        )
        for item in response.items or []:
            yield item
        key = response.last_evaluated_key
        if not key:
            break
