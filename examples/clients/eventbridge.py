import asyncio

from rboto import EventBridgeClient
from rboto_eventbridge.types import PutEventsRequestEntry


async def main() -> None:
    client = EventBridgeClient(region="us-east-1")
    entry: PutEventsRequestEntry = {
        "event_bus_name": "default",
        "source": "example.orders",
        "detail_type": "OrderCreated",
        "detail": '{"order_id":"123"}',
    }
    response = await client.put_events(entries=[entry])
    if response.failed_entry_count:
        raise RuntimeError(f"Event publication failed: {response.entries}")
    print(response.entries)


if __name__ == "__main__":
    asyncio.run(main())
