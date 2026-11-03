import asyncio

from rboto import DynamoDBClient, WaiterConfig
from rboto.waiters import WaiterFailedError, WaiterTimeoutError


async def main() -> None:
    client = DynamoDBClient(region="us-east-1")
    # The table must already have been created, for example with create_table().
    try:
        output = await client.waiters.table_exists(
            table_name="example",
            waiter_config=WaiterConfig(delay=2, max_attempts=30, timeout=60),
        )
    except WaiterTimeoutError as error:
        print(error.reason, error.attempts, error.last_attempt)
    except WaiterFailedError as error:
        print(error.waiter_name, error.last_attempt)
    else:
        if output.table is not None:
            print(output.table.table_status)


if __name__ == "__main__":
    asyncio.run(main())
