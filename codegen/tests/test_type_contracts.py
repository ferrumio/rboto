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
