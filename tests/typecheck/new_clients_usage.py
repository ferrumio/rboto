from collections.abc import AsyncIterator

from rboto import bedrock_runtime, lambda_, secrets_manager, sts
from rboto_bedrock_runtime.types import InvokeModelWithBidirectionalStreamInput, JSONValue


async def identity_and_secrets() -> str | None:
    identity = await sts(region="us-east-1").get_caller_identity()
    account: str | None = identity.account
    secret = await secrets_manager().get_secret_value(secret_id="example")
    value: str | None = secret.secret_string
    await lambda_().invoke(function_name="example", payload=b"{}")
    return value or account


async def converse() -> JSONValue:
    response = await bedrock_runtime().converse(
        model_id="example",
        messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        additional_model_request_fields={"temperature": 0.5, "enabled": True},
    )
    assert response.usage is not None
    tokens: int = response.usage.input_tokens
    assert tokens >= 0
    return response.additional_model_response_fields


async def duplex(
    events: AsyncIterator[InvokeModelWithBidirectionalStreamInput],
) -> None:
    response = await bedrock_runtime().invoke_model_with_bidirectional_stream(
        model_id="example", body=events
    )
    async for event in response["body"]:
        print(event)
