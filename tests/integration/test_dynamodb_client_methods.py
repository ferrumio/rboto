import asyncio
import os
import uuid

import pytest
from rboto import dynamodb
from rboto_dynamodb import DynamoDBClient
from rboto_dynamodb.exceptions import ResourceNotFoundError
from rboto_dynamodb.types import AttributeValue


async def wait_until_active(client: DynamoDBClient, table_name: str) -> None:
    for _ in range(40):
        response = await client.describe_table(table_name=table_name)
        if response.table is not None and response.table.table_status == "ACTIVE":
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"table did not become active: {table_name}")


@pytest.mark.asyncio
async def test_dynamodb_client_methods_reach_rust_and_localstack() -> None:
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url is None:
        pytest.skip("AWS_ENDPOINT_URL is required for integration tests")

    client = dynamodb(region="us-east-1", endpoint_url=endpoint_url)
    table_name = f"rboto-native-output-{uuid.uuid4().hex[:12]}"
    created = await client.create_table(
        table_name=table_name,
        attribute_definitions=[{"attribute_name": "pk", "attribute_type": "S"}],
        key_schema=[{"attribute_name": "pk", "key_type": "HASH"}],
        billing_mode="PAY_PER_REQUEST",
    )
    assert created.table_description is not None

    try:
        await wait_until_active(client, table_name)
        item: dict[str, AttributeValue] = {
            "pk": {"s": "USER#1"},
            "name": {"s": "Ada"},
        }
        await client.put_item(table_name=table_name, item=item)

        fetched = await client.get_item(
            table_name=table_name,
            key={"pk": {"s": "USER#1"}},
        )
        assert fetched.item == item

        updated = await client.update_item(
            table_name=table_name,
            key={"pk": {"s": "USER#1"}},
            update_expression="SET #name = :name",
            expression_attribute_names={"#name": "name"},
            expression_attribute_values={":name": {"s": "Grace"}},
            return_values="ALL_NEW",
        )
        assert updated.attributes is not None
        assert updated.attributes["name"] == {"s": "Grace"}

        queried = await client.query(
            table_name=table_name,
            key_condition_expression="pk = :pk",
            expression_attribute_values={":pk": {"s": "USER#1"}},
        )
        assert queried.count == 1
        assert queried.items is not None

        scanned = await client.scan(table_name=table_name)
        assert scanned.count == 1

        batch = await client.batch_get_item(
            request_items={
                table_name: {"keys": [{"pk": {"s": "USER#1"}}]},
            }
        )
        assert batch.responses is not None
        assert len(batch.responses[table_name]) == 1

        await client.batch_write_item(
            request_items={
                table_name: [
                    {
                        "put_request": {
                            "item": {"pk": {"s": "USER#2"}, "name": {"s": "Linus"}}
                        }
                    }
                ]
            }
        )
        await client.transact_write_items(
            transact_items=[
                {
                    "put": {
                        "table_name": table_name,
                        "item": {"pk": {"s": "USER#3"}, "name": {"s": "Margaret"}},
                    }
                }
            ]
        )

        expanded_scan = await client.scan(table_name=table_name)
        assert expanded_scan.count == 3

        tables = await client.list_tables()
        assert tables.table_names is not None
        assert table_name in tables.table_names

        with pytest.raises(ResourceNotFoundError):
            await client.get_item(
                table_name=f"{table_name}-missing",
                key={"pk": {"s": "USER#1"}},
            )
    finally:
        await client.delete_table(table_name=table_name)
