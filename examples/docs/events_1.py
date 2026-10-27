import json

from rboto import eventbridge
from rboto_eventbridge.types import PutEventsRequestEntry


async def publish_order(bus_name: str, order_id: str) -> list[str]:
    entry: PutEventsRequestEntry = {
        "event_bus_name": bus_name,
        "source": "myapp.orders",
        "detail_type": "OrderCreated",
        "detail": json.dumps({"order_id": order_id}),
    }
    response = await eventbridge(region="us-east-1").put_events(entries=[entry])
    entries = response.entries or []
    if response.failed_entry_count:
        failures = [
            (item.error_code, item.error_message) for item in entries if item.error_code is not None
        ]
        raise RuntimeError(f"Event publication failed: {failures}")
    return [item.event_id for item in entries if item.event_id is not None]
