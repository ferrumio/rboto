from rboto_bedrock_runtime import BedrockRuntimeClient
from rboto_bedrock_runtime.types import Message


async def converse(client: BedrockRuntimeClient, model_id: str) -> None:
    message: Message = {"role": "user", "content": [{"text": "Explain this in one sentence."}]}
    response = await client.converse(model_id=model_id, messages=[message])
    print(response.output)
    print(response.usage)
