from rboto import BedrockRuntimeClient
from rboto_bedrock_runtime import inputs
from rboto_bedrock_runtime.types import Message


async def append_reply(
    client: BedrockRuntimeClient, model_id: str, messages: list[Message],
) -> None:
    response = await client.converse(model_id=model_id, messages=messages)
    output = response.output
    if output is None or "unknown" in output:
        raise ValueError("Expected a message response")
    messages.append(inputs.message(output["message"]))
