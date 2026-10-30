"""Check the public types as a consumer, without loading native extensions."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def check_consumer(path: Path, source: str) -> subprocess.CompletedProcess[str]:
    path.write_text(source)
    sources = [ROOT / "packages/rboto/src", *sorted(ROOT.glob("packages/rboto-*/python"))]
    return subprocess.run(
        [
            sys.executable, "-m", "mypy", "--strict", "--no-incremental",
            "--follow-imports=silent", str(path),
        ],
        cwd=ROOT,
        env={**os.environ, "MYPYPATH": os.pathsep.join(str(item) for item in sources)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_response_and_sequence_usage(tmp_path: Path) -> None:
    result = check_consumer(tmp_path / "valid.py", """
from typing import assert_type
from rboto import DynamoDBClient, SQSClient, UnknownEnumValue
from rboto_bedrock_runtime.types import ToolUseBlockOutput
from rboto_sqs import GetQueueAttributesResult

sample: ToolUseBlockOutput = {
    "tool_use_id": "call-1", "name": "lookup", "input": {}, "type": None,
}

def read_tool(block: ToolUseBlockOutput) -> str:
    kind = block["type"]
    return kind.upper() if kind is not None else "unspecified"

def read_attributes(response: GetQueueAttributesResult) -> str | None:
    if response.attributes is None:
        return None
    assert_type(response.attributes["VisibilityTimeout"], str)
    return response.attributes.get(UnknownEnumValue("FutureAttribute"))

async def send(sqs: SQSClient, ddb: DynamoDBClient, url: str) -> None:
    await sqs.receive_message(
        queue_url=url, message_system_attribute_names=("SentTimestamp",),
    )
    await ddb.put_item(
        table_name="Example", item={"pk": {"s": "example"}, "tags": {"ss": ("a", "b")}},
    )
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_inputs_and_unsafe_response_usage_are_rejected(tmp_path: Path) -> None:
    result = check_consumer(tmp_path / "invalid.py", """
from rboto import DynamoDBClient
from rboto_bedrock_runtime.types import ToolUseBlock, ToolUseBlockOutput
from rboto_sqs import GetQueueAttributesResult

def unsafe_tool(block: ToolUseBlockOutput) -> None:
    print(block["type"].upper())

def misspelled_attribute(response: GetQueueAttributesResult) -> None:
    if response.attributes is not None:
        print(response.attributes["VisibilityTimout"])

async def unknown_input(client: DynamoDBClient) -> None:
    await client.put_item(table_name="Example", item={"pk": {"unknown": True}})

async def nested_unknown_input(client: DynamoDBClient) -> None:
    await client.put_item(
        table_name="Example", item={"pk": {"m": {"child": {"unknown": True}}}},
    )

invalid_input: ToolUseBlock = {
    "tool_use_id": "call-1", "name": "lookup", "input": {}, "type": None,
}
""")
    assert result.returncode == 1, result.stdout + result.stderr
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 5, result.stdout
    for code in ("[union-attr]", "[index]", "[dict-item]", "[typeddict-item]"):
        assert any(code in error for error in errors), result.stdout


def test_paginators_reject_invalid_arguments_and_unknown_operations(tmp_path: Path) -> None:
    result = check_consumer(tmp_path / "invalid_paginators.py", """
from rboto import DynamoDBClient, LambdaClient, S3Client

def invalid(ddb: DynamoDBClient, lamb: LambdaClient, s3: S3Client) -> None:
    lamb.paginators.list_functions(max_items="ten")
    lamb.paginators.list_functions(max_itmes=10)
    lamb.paginators.invoke(function_name="example")
    ddb.paginators.query()
    ddb.paginators.query(table_name="example", exclusive_start_key={"pk": {"unknown": True}})
    s3.paginators.list_objects_v2(bucket=5)
""")
    assert result.returncode == 1, result.stdout + result.stderr
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 6, result.stdout


def test_recursive_json_alias_preserves_strict_input_validation(tmp_path: Path) -> None:
    result = check_consumer(tmp_path / "json_types.py", """
from rboto import BedrockRuntimeClient
from rboto_bedrock_runtime.types import JSONValue

valid: JSONValue = {"nested": [None, True, 1, 1.5, "text", {"items": []}]}
invalid_key: JSONValue = {1: "not a string key"}
invalid_value: JSONValue = {"nested": object()}

async def send(client: BedrockRuntimeClient) -> None:
    await client.converse(model_id="example", additional_model_request_fields=valid)
    await client.converse(model_id="example", additional_model_request_fields={"bad": object()})
""")
    assert result.returncode == 1, result.stdout + result.stderr
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 3, result.stdout


def test_response_types_are_public_alongside_input_types(tmp_path: Path) -> None:
    result = check_consumer(tmp_path / "public_types.py", """
from collections.abc import AsyncIterator
from typing import assert_type
from rboto import BedrockRuntimeClient, LambdaClient, S3Client
from rboto_s3 import CreateBucketOutput as LegacyCreateBucketOutput
from rboto_s3.types import (
    Bucket, BucketOutput, ByteStream, CreateBucketOutput, CreateBucketRequest,
    GetObjectOutput, ListBucketsOutput, ListMultipartUploadsOutput,
)
from rboto_lambda.types import FunctionConfiguration, ListFunctionsResponse
from rboto_bedrock_runtime.types import ConverseStreamOutput, ConverseStreamResponse

def describe_bucket(bucket: BucketOutput) -> str | None:
    return bucket.name

async def create(client: S3Client) -> CreateBucketOutput:
    request: CreateBucketRequest = {"bucket": "example"}
    response = await client.create_bucket(bucket=request["bucket"])
    assert_type(response, CreateBucketOutput)
    legacy: LegacyCreateBucketOutput = response
    return legacy

async def consume(s3: S3Client, lamb: LambdaClient, bedrock: BedrockRuntimeClient) -> None:
    request_bucket: Bucket = {"name": "example"}
    assert_type(request_bucket["name"], str)
    buckets = await s3.list_buckets()
    assert_type(buckets, ListBucketsOutput)
    for bucket in buckets.buckets or []:
        assert_type(bucket, BucketOutput)
        describe_bucket(bucket)
    obj = await s3.get_object(bucket="example", key="key")
    assert_type(obj, GetObjectOutput)
    assert_type(obj.body, ByteStream)
    functions = await lamb.list_functions()
    assert_type(functions, ListFunctionsResponse)
    for function in functions.functions or []:
        assert_type(function, FunctionConfiguration)
    async for page in s3.paginators.list_multipart_uploads(bucket="example"):
        assert_type(page, ListMultipartUploadsOutput)
    streaming = await bedrock.converse_stream(model_id="example")
    assert_type(streaming, ConverseStreamResponse)
    assert_type(streaming["stream"], AsyncIterator[ConverseStreamOutput])
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_output_aliases_preserve_readonly_fields_and_input_distinction(tmp_path: Path) -> None:
    result = check_consumer(tmp_path / "invalid_public_types.py", """
from rboto import S3Client
from rboto_s3.types import Bucket, BucketOutput, CreateBucketRequest

async def invalid(client: S3Client, output: BucketOutput) -> None:
    request: CreateBucketRequest = await client.create_bucket(bucket="example")
    output.name = "changed"
    mapping: Bucket = output
    print(request, mapping)
""")
    assert result.returncode == 1, result.stdout + result.stderr
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 3, result.stdout
