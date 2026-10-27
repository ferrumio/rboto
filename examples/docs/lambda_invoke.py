import json

from rboto_lambda import LambdaClient


async def invoke(client: LambdaClient, function_name: str) -> bytes:
    response = await client.invoke(
        function_name=function_name,
        invocation_type="RequestResponse",
        payload=json.dumps({"order_id": "order-123"}).encode(),
    )
    payload = response.payload or b""
    if response.function_error is not None:
        raise RuntimeError(f"Function failed: {response.function_error}")
    return payload
