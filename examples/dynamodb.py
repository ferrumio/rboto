import asyncio
import os
import sys
import uuid
from pathlib import Path

PROJECT_VENV = Path(__file__).resolve().parents[1] / ".venv"
if Path(sys.prefix).resolve() != PROJECT_VENV.resolve():
    venv_python = PROJECT_VENV / "bin" / "python"
    if not venv_python.exists():
        raise RuntimeError("project virtual environment not found: run python3 -m venv .venv")
    os.execv(venv_python, [str(venv_python), *sys.argv])

from rboto import dynamodb
from rboto_dynamodb import DynamoDBClient
from rboto_dynamodb.types import AttributeValue


def configure_aws() -> str:
    return os.getenv("AWS_REGION", "us-east-1")


async def wait_until_active(client: DynamoDBClient, table_name: str) -> None:
    for _ in range(40):
        response = await client.describe_table(table_name=table_name)
        description = response.get("table")
        if description is not None and description.get("table_status") == "ACTIVE":
            return
        await asyncio.sleep(0.25)
    raise TimeoutError(f"table did not become active: {table_name}")


async def main() -> None:
    region = configure_aws()
    client = dynamodb(region=region)
    table_name = f"rboto-example-{uuid.uuid4().hex[:12]}"
    table_created = False

    try:
        created = await client.create_table(
            table_name=table_name,
            attribute_definitions=[
                {"attribute_name": "pk", "attribute_type": "S"},
            ],
            key_schema=[
                {"attribute_name": "pk", "key_type": "HASH"},
            ],
            billing_mode="PAY_PER_REQUEST",
        )
        table_created = True
        print("created table:", created.get("table_description", {}).get("table_name"))
        await wait_until_active(client, table_name)

        item: dict[str, AttributeValue] = {
            "pk": {"s": "USER#1"},
            "name": {"s": "Ada"},
            "age": {"n": "37"},
            "active": {"bool": True},
            "roles": {"ss": ["admin", "developer"]},
        }
        await client.put_item(table_name=table_name, item=item)
        print("inserted item:", item)

        key: dict[str, AttributeValue] = {"pk": {"s": "USER#1"}}
        fetched = await client.get_item(table_name=table_name, key=key)
        print("fetched item:", fetched.get("item"))

        queried = await client.query(
            table_name=table_name,
            key_condition_expression="pk = :pk",
            expression_attribute_values={":pk": {"s": "USER#1"}},
        )
        print("query items:", queried.get("items", []))
    finally:
        if table_created:
            await client.delete_table(table_name=table_name)
            print("deleted table:", table_name)


if __name__ == "__main__":
    asyncio.run(main())
