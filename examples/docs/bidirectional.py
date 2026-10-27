from collections.abc import AsyncIterator

from rboto_bedrock_runtime import BedrockRuntimeClient
from rboto_bedrock_runtime.types import InvokeModelWithBidirectionalStreamInput


async def stream(
    client: BedrockRuntimeClient,
    model_id: str,
    events: AsyncIterator[InvokeModelWithBidirectionalStreamInput],
) -> None:
    response = await client.invoke_model_with_bidirectional_stream(model_id=model_id, body=events)
    async for event in response["body"]:
        print(event)
