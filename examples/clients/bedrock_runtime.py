import asyncio

from rboto import BedrockRuntimeClient
from rboto_bedrock_runtime.types import Message


async def main() -> None:
    client = BedrockRuntimeClient(region="us-east-1")
    message: Message = {"role": "user", "content": [{"text": "Explain async I/O briefly."}]}
    response = await client.converse(model_id="YOUR_MODEL_ID", messages=[message])
    print(response.output)
    print(response.usage)


if __name__ == "__main__":
    asyncio.run(main())
