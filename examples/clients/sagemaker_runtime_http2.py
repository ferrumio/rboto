import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from rboto import SageMakerRuntimeHTTP2Client
from rboto_sagemaker_runtime_http2.types import RequestStreamEvent


async def request_events() -> AsyncIterator[RequestStreamEvent]:
    # Use an endpoint whose model accepts this JSON request format.
    # Models may require several payload parts or a different content format.
    payload = await asyncio.to_thread(Path("request.json").read_bytes)
    yield {
        "payload_part": {
            "bytes": payload,
            "data_type": "UTF8",
            "completion_state": "COMPLETE",
        },
    }


async def main() -> None:
    client = SageMakerRuntimeHTTP2Client(region="us-east-1")
    response = await client.invoke_endpoint_with_bidirectional_stream(
        endpoint_name="my-streaming-endpoint",
        body=request_events(),
    )
    async for event in response["body"]:
        print(event)


if __name__ == "__main__":
    asyncio.run(main())
