from collections.abc import AsyncIterator

from rboto import DynamoDBClient
from rboto_dynamodb.types import AttributeValue, AttributeValueOutput


def continuation_key(values: dict[str, AttributeValueOutput]) -> dict[str, AttributeValue]:
    # DynamoDB keys contain string, number, or binary scalar values.
    key: dict[str, AttributeValue] = {}
    for name, value in values.items():
        match value:
            case {"s": str(text)}:
                key[name] = {"s": text}
            case {"n": str(number)}:
                key[name] = {"n": number}
            case {"b": bytes(binary)}:
                key[name] = {"b": binary}
            case _:
                raise ValueError(f"Unsupported continuation key: {name}")
    return key


async def query_items(
    client: DynamoDBClient,
    table: str,
    partition: str,
) -> AsyncIterator[dict[str, AttributeValueOutput]]:
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
        if not response.last_evaluated_key:
            break
        key = continuation_key(response.last_evaluated_key)
