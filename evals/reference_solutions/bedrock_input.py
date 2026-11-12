from typing import cast

from rboto import BedrockRuntimeClient
from rboto_bedrock_runtime.types import ConverseValueOutputmessage


async def run(endpoint_url: str, model_id: str, prompt: str) -> str:
    async with BedrockRuntimeClient(region="eu-west-2", endpoint_url=endpoint_url) as client:
        response = await client.converse(
            model_id=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inference_config={"max_tokens": 32, "temperature": 0},
        )
        output = response.output
        if output is None or "message" not in output:
            return ""
        message = cast(ConverseValueOutputmessage, output)["message"]
        parts: list[str] = []
        for block in message["content"]:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
