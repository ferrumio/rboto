"""Opt-in tests against AWS. See README.md before running."""

import asyncio
import io
import json
import os
import time
import uuid
import zipfile
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from rboto import bedrock_runtime, lambda_
from rboto_bedrock_runtime.client import BedrockRuntimeClient
from rboto_bedrock_runtime.exceptions import ValidationError
from rboto_bedrock_runtime.types import Message
from rboto_lambda.client import LambdaClient
from rboto_lambda.exceptions import InvalidParameterValueError, ResourceNotFoundError

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RBOTO_LIVE_AWS") != "1",
        reason="Requires explicit RBOTO_LIVE_AWS=1; creates temporary AWS resources.",
    ),
]

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL = os.environ.get("RBOTO_LIVE_MODEL", "amazon.nova-micro-v1:0")
TOKEN_MODEL = os.environ.get(
    "RBOTO_LIVE_TOKEN_MODEL", "anthropic.claude-haiku-4-5-20251001-v1:0"
)
MESSAGES: list[Message] = [
    {"role": "user", "content": [{"text": "Reply with exactly RBOTO_OK."}]}
]
NATIVE_BODY = json.dumps(
    {
        "schemaVersion": "messages-v1",
        "messages": MESSAGES,
        "inferenceConfig": {"maxTokens": 32, "temperature": 0.0},
    }
).encode()


@pytest.fixture
def bedrock_client() -> BedrockRuntimeClient:
    return bedrock_runtime(
        region=REGION, endpoint_url=f"https://bedrock-runtime.{REGION}.amazonaws.com"
    )


async def test_bedrock_converse_native_outputs(bedrock_client: BedrockRuntimeClient) -> None:
    result = await bedrock_client.converse(
        model_id=MODEL,
        messages=MESSAGES,
        inference_config={"max_tokens": 32, "temperature": 0.0},
    )
    assert result.output is not None
    output = result.to_dict()
    assert output["output"]["message"]["content"][0]["text"]
    assert result.usage is not None
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.usage.total_tokens == result.usage.input_tokens + result.usage.output_tokens
    assert result.stop_reason in {"end_turn", "max_tokens"}
    with pytest.raises(AttributeError):
        result.usage.input_tokens = 0


async def test_bedrock_converse_stream(bedrock_client: BedrockRuntimeClient) -> None:
    result = await bedrock_client.converse_stream(
        model_id=MODEL,
        messages=MESSAGES,
        inference_config={"max_tokens": 32, "temperature": 0.0},
    )
    events = [event async for event in result["stream"]]
    text = "".join(
        event["content_block_delta"]["delta"].get("text", "")
        for event in events
        if "content_block_delta" in event
    )
    assert text
    assert any("message_stop" in event for event in events)
    metadata = next(event["metadata"] for event in events if "metadata" in event)
    assert metadata["usage"]["input_tokens"] > 0
    with pytest.raises(StopAsyncIteration):
        await anext(result["stream"])


async def test_bedrock_invoke_model(bedrock_client: BedrockRuntimeClient) -> None:
    result = await bedrock_client.invoke_model(
        model_id=MODEL, body=NATIVE_BODY, content_type="application/json"
    )
    assert result.content_type == "application/json"
    body = json.loads(result.body)
    assert body["output"]["message"]["content"][0]["text"]
    assert body["usage"]["outputTokens"] > 0


async def test_bedrock_invoke_model_stream(bedrock_client: BedrockRuntimeClient) -> None:
    result = await bedrock_client.invoke_model_with_response_stream(
        model_id=MODEL, body=NATIVE_BODY, content_type="application/json"
    )
    events = [
        json.loads(event["chunk"]["bytes"])
        async for event in result["body"]
        if "chunk" in event
    ]
    assert "".join(
        event["contentBlockDelta"]["delta"].get("text", "")
        for event in events
        if "contentBlockDelta" in event
    )
    assert any("messageStop" in event for event in events)
    with pytest.raises(StopAsyncIteration):
        await anext(result["body"])


@pytest.mark.parametrize("input_variant", ["converse", "invoke_model"])
async def test_bedrock_count_tokens(
    bedrock_client: BedrockRuntimeClient, input_variant: str
) -> None:
    if input_variant == "converse":
        token_input = {"converse": {"messages": MESSAGES}}
    else:
        token_input = {
            "invoke_model": {
                "body": json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 16,
                        "messages": [
                            {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
                        ],
                    }
                ).encode()
            }
        }
    result = await bedrock_client.count_tokens(model_id=TOKEN_MODEL, input=token_input)
    assert result.input_tokens > 0
    assert result.to_dict()["input_tokens"] == result.input_tokens


async def test_bedrock_tool_document(bedrock_client: BedrockRuntimeClient) -> None:
    result = await bedrock_client.converse(
        model_id=MODEL,
        messages=[
            {
                "role": "user",
                "content": [{"text": "Call the echo tool with value RBOTO_OK. Do not answer."}],
            }
        ],
        inference_config={"max_tokens": 64, "temperature": 0.0},
        tool_config={
            "tools": [
                {
                    "tool_spec": {
                        "name": "echo",
                        "description": "Echo the provided value.",
                        "input_schema": {
                            "json": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            }
                        },
                    }
                }
            ]
        },
    )
    assert result.stop_reason == "tool_use"
    content = result.to_dict()["output"]["message"]["content"]
    tool = next(block["tool_use"] for block in content if "tool_use" in block)
    assert tool["name"] == "echo"
    assert tool["input"] == {"value": "RBOTO_OK"}


async def test_bedrock_concurrent_calls(bedrock_client: BedrockRuntimeClient) -> None:
    results = await asyncio.gather(
        *(
            bedrock_client.converse(
                model_id=MODEL,
                messages=MESSAGES,
                inference_config={"max_tokens": 16, "temperature": 0.0},
            )
            for _ in range(3)
        )
    )
    assert all(result.usage and result.usage.output_tokens > 0 for result in results)
    assert len({id(result) for result in results}) == 3


async def test_bedrock_independent_streams(bedrock_client: BedrockRuntimeClient) -> None:
    async def consume() -> str:
        result = await bedrock_client.converse_stream(
            model_id=MODEL,
            messages=MESSAGES,
            inference_config={"max_tokens": 16, "temperature": 0.0},
        )
        return "".join(
            [
                event["content_block_delta"]["delta"].get("text", "")
                async for event in result["stream"]
                if "content_block_delta" in event
            ]
        )

    first, second = await asyncio.gather(consume(), consume())
    assert first and second


async def test_bedrock_modeled_error(bedrock_client: BedrockRuntimeClient) -> None:
    with pytest.raises(ValidationError) as caught:
        await bedrock_client.converse(
            model_id="rboto-nonexistent-model", messages=MESSAGES
        )
    assert caught.value.error_code == "ValidationException"
    assert caught.value.operation_name == "Converse"


async def aws_cli(*arguments: str) -> dict:
    process = await asyncio.create_subprocess_exec(
        "aws",
        "--region",
        REGION,
        "--no-cli-pager",
        "--output",
        "json",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode())
    return json.loads(stdout) if stdout.strip() else {}


def function_zip(revision: int) -> bytes:
    source = f"""
exports.handler = async (event) => {{
    if (event.fail) throw new Error("rboto-intentional-error");
    return {{value: event.value, revision: {revision}, env: process.env.RBOTO_TEST}};
}};
exports.stream = awslambda.streamifyResponse(async (event, responseStream) => {{
    responseStream.setContentType("application/json");
    responseStream.write('{{"value":');
    await new Promise(resolve => setTimeout(resolve, 25));
    responseStream.end(JSON.stringify(event.value) + '}}');
}});
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.js", source)
    return buffer.getvalue()


async def wait_for_function(client: LambdaClient, name: str) -> None:
    async with asyncio.timeout(180):
        while True:
            config = await client.get_function_configuration(function_name=name)
            assert config.state != "Failed", config.state_reason
            assert config.last_update_status != "Failed", config.last_update_status_reason
            if config.state == "Active" and config.last_update_status in {None, "Successful"}:
                return
            await asyncio.sleep(2)


@pytest_asyncio.fixture
async def live_function() -> AsyncIterator[tuple[LambdaClient, str, str]]:
    name = f"rboto-live-{uuid.uuid4().hex[:12]}"
    client = lambda_(region=REGION, endpoint_url=f"https://lambda.{REGION}.amazonaws.com")
    role_created = False
    function_attempted = False
    try:
        role = await aws_cli(
            "iam",
            "create-role",
            "--role-name",
            name,
            "--description",
            "Temporary rboto live test; no permission policies",
            "--assume-role-policy-document",
            json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )
        role_created = True
        deadline = time.monotonic() + 90
        while True:
            try:
                function_attempted = True
                created = await client.create_function(
                    function_name=name,
                    role=role["Role"]["Arn"],
                    code={"zip_file": function_zip(1)},
                    runtime="nodejs22.x",
                    handler="index.handler",
                    timeout=10,
                    memory_size=128,
                    architectures=["arm64"],
                    environment={"variables": {"RBOTO_TEST": "initial"}},
                    tags={"purpose": "rboto-live-test"},
                )
                break
            except InvalidParameterValueError as error:
                if "cannot be assumed" not in error.message or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(3)
        assert created.function_name == name
        assert created.function_arn
        print(f"\nPASS Lambda CreateFunction ({name})", flush=True)
        await wait_for_function(client, name)
        yield client, name, created.function_arn
    finally:
        try:
            if function_attempted:
                try:
                    await client.delete_function(function_name=name)
                except ResourceNotFoundError:
                    pass
                with pytest.raises(ResourceNotFoundError):
                    await client.get_function(function_name=name)
                print("\nPASS Lambda DeleteFunction (absence verified)", flush=True)
        finally:
            if role_created:
                await aws_cli("iam", "delete-role", "--role-name", name)
                with pytest.raises(RuntimeError, match="NoSuchEntity"):
                    await aws_cli("iam", "get-role", "--role-name", name)
                print("PASS IAM DeleteRole (absence verified)", flush=True)


async def test_lambda_lifecycle(live_function: tuple[LambdaClient, str, str]) -> None:
    client, name, arn = live_function
    async with asyncio.timeout(300):
        account = await client.get_account_settings()
        assert account.account_limit is not None
        found = await client.get_function(function_name=name)
        assert found.configuration and found.configuration.function_name == name
        assert found.configuration.runtime == "nodejs22.x"
        tags = await client.list_tags(resource=arn)
        assert tags.tags and tags.tags["purpose"] == "rboto-live-test"
        await client.tag_resource(resource=arn, tags={"roundtrip": "yes"})
        assert (await client.list_tags(resource=arn)).tags["roundtrip"] == "yes"
        await client.untag_resource(resource=arn, tag_keys=["roundtrip"])
        assert "roundtrip" not in (await client.list_tags(resource=arn)).tags
        print("PASS Lambda GetAccountSettings/GetFunction/ListTags/TagResource/UntagResource")

        async def invoke(value: str, *, qualifier: str | None = None) -> dict:
            result = await client.invoke(
                function_name=name,
                qualifier=qualifier,
                payload=json.dumps({"value": value}).encode(),
            )
            assert result.status_code == 200
            assert result.function_error is None
            assert result.payload is not None
            return json.loads(result.payload)

        results = await asyncio.gather(*(invoke(str(i)) for i in range(3)))
        assert results == [
            {"value": str(i), "revision": 1, "env": "initial"} for i in range(3)
        ]
        dry_run = await client.invoke(
            function_name=name, invocation_type="DryRun", payload=b"{}"
        )
        assert dry_run.status_code == 204
        failed = await client.invoke(function_name=name, payload=b'{"fail":true}')
        assert failed.status_code == 200 and failed.function_error == "Unhandled"
        assert json.loads(failed.payload)["errorMessage"] == "rboto-intentional-error"
        print("PASS Lambda Invoke (concurrency, payload, DryRun, function error)")

        await client.update_function_configuration(
            function_name=name, environment={"variables": {"RBOTO_TEST": "updated"}}
        )
        await wait_for_function(client, name)
        assert (await invoke("config"))["env"] == "updated"
        first = await client.publish_version(function_name=name)
        assert first.version and first.version != "$LATEST"
        alias = await client.create_alias(
            function_name=name, name="live-test", function_version=first.version
        )
        assert alias.function_version == first.version
        assert (await invoke("alias", qualifier="live-test"))["revision"] == 1
        await client.update_function_code(function_name=name, zip_file=function_zip(2))
        await wait_for_function(client, name)
        assert (await invoke("code"))["revision"] == 2
        assert (await invoke("old", qualifier="live-test"))["revision"] == 1
        second = await client.publish_version(function_name=name)
        assert second.version and second.version != first.version
        await client.update_alias(
            function_name=name, name="live-test", function_version=second.version
        )
        assert (await invoke("new", qualifier="live-test"))["revision"] == 2
        aliases = await client.list_aliases(function_name=name)
        assert any(alias.name == "live-test" for alias in aliases.aliases or [])
        versions = await client.list_versions_by_function(function_name=name)
        assert {first.version, second.version} <= {
            version.version for version in versions.versions or []
        }
        print("PASS Lambda config/code updates, versions, aliases and version isolation")

        await client.update_function_configuration(function_name=name, handler="index.stream")
        await wait_for_function(client, name)
        streamed = await client.invoke_with_response_stream(
            function_name=name, payload=b'{"value":"stream-ok"}'
        )
        assert streamed["status_code"] == 200
        events = [event async for event in streamed["event_stream"]]
        payload = b"".join(
            event["payload_chunk"]["payload"] or b""
            for event in events
            if "payload_chunk" in event
        )
        assert json.loads(payload) == {"value": "stream-ok"}
        completed = next(event["invoke_complete"] for event in events if "invoke_complete" in event)
        assert not completed.get("error_code")
        with pytest.raises(StopAsyncIteration):
            await anext(streamed["event_stream"])
        await client.delete_alias(function_name=name, name="live-test")
        with pytest.raises(ResourceNotFoundError) as caught:
            await client.get_alias(function_name=name, name="live-test")
        assert caught.value.error_code == "ResourceNotFoundException"
        assert caught.value.operation_name == "GetAlias"
        print("PASS Lambda InvokeWithResponseStream, DeleteAlias and modeled error")
