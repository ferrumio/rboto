from collections.abc import AsyncIterator

from rboto import DynamoDBClient
from rboto_dynamodb.types import AttributeValueOutput


async def query_items(
    client: DynamoDBClient,
    table: str,
    partition: str,
) -> AsyncIterator[dict[str, AttributeValueOutput]]:
    async for page in client.paginators.query(
        table_name=table,
        key_condition_expression="pk = :pk",
        expression_attribute_values={":pk": {"s": partition}},
    ):
        for item in page.items or []:
            yield item
