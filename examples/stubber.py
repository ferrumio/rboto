"""Run application tests with native Outputs and no service calls."""

import asyncio
from typing import assert_type

from rboto import DynamoDBClient
from rboto.testing import Stubber
from rboto_dynamodb.exceptions import ResourceNotFoundError
from rboto_dynamodb.types import GetItemOutput


async def main() -> None:
    client = DynamoDBClient()
    with Stubber(client) as stub:
        stub.add_response(
            client.get_item,
            {"item": {"pk": {"s": "user-1"}, "name": {"s": "Ada"}}},
            table_name="users",
            key={"pk": {"s": "user-1"}},
        )
        result = await client.get_item(
            table_name="users", key={"pk": {"s": "user-1"}},
        )
        assert_type(result, GetItemOutput)
        assert result.item == {"pk": {"s": "user-1"}, "name": {"s": "Ada"}}

        stub.add_error(
            client.describe_table,
            ResourceNotFoundError("Table missing", error_code="ResourceNotFoundException"),
            table_name="missing",
        )
        try:
            await client.describe_table(table_name="missing")
        except ResourceNotFoundError as error:
            assert error.operation_name == "DescribeTable"
        else:
            raise AssertionError("Expected ResourceNotFoundError")


if __name__ == "__main__":
    asyncio.run(main())
