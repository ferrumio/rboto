"""Public tasks and documentation selection. No solutions or grading cases."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Task:
    name: str
    service: str
    prompt: str
    references: tuple[str, ...]
    guides: tuple[str, ...]
    pydantic: bool = False

    def dependencies(self, version: str) -> list[str]:
        result = [f"rboto[{self.service}]=={version}"]
        if self.pydantic:
            result.append("pydantic==2.14.3")
        return result


TASKS = (
    Task(
        "s3_projection",
        "s3",
        """Implement `async def run(endpoint_url: str) -> list[BucketSummary]`.
Define BucketSummary as a Pydantic BaseModel with `name: str`.
Call S3 list_buckets once. Return a model for each bucket with a non-null name,
in response order. An absent bucket list returns []. Project native attributes
with Pydantic's attribute validation, without converting the response to a dict.
Use region eu-west-2 and the supplied endpoint. Close the client after use.""",
        ("s3/operations/list_buckets", "s3/types/BucketOutput"),
        ("types", "models", "lifecycle"),
        True,
    ),
    Task(
        "dynamodb_pages",
        "dynamodb",
        """Implement `async def run(endpoint_url: str, table_name: str,
partition: str) -> list[str]`. Query every page, including continuation through
an empty intermediate page. Use key_condition_expression '#pk = :pk',
expression_attribute_names {'#pk': 'pk'}, and a DynamoDB string attribute for
the partition value. Collect the string `label` attributes in order, ignoring
absent or non-string labels. Use region eu-west-2, the supplied endpoint/table,
and close the client. The provided partition value must reach every request.""",
        (
            "dynamodb/operations/query",
            "dynamodb/types/AttributeValue",
            "dynamodb/types/AttributeValueOutput",
        ),
        ("pagination", "dynamodb", "types", "lifecycle"),
    ),
    Task(
        "sqs_ack",
        "sqs",
        """Implement `async def run(endpoint_url: str, queue_url: str,
handler: Callable[[str], Awaitable[None]]) -> int`.
Receive one batch of at most 10 messages. Await handler(body) for each message
that has both a body and a receipt handle. Delete each message only after its
handler succeeds. Return the number deleted. An absent message list returns 0.
If a handler fails, propagate its original exception and do not delete that
message or process later messages. Skip messages missing body or receipt handle;
an empty-string body is valid. Use region eu-west-2 and close the client.""",
        (
            "sqs/operations/receive_message",
            "sqs/operations/delete_message",
            "sqs/types/MessageOutput",
        ),
        ("messaging", "types", "lifecycle"),
    ),
    Task(
        "s3_stream",
        "s3",
        """Implement `async def run(endpoint_url: str, bucket: str,
key: str) -> str | None`. Return the SHA-256 hex digest of the complete object.
Consume its body with async iteration, without collecting the whole body or
using read(). Explicitly close the response stream and client. Return None for
a modeled NoSuchKey error only; propagate other errors. Use region eu-west-2
and the supplied endpoint/bucket/key.""",
        ("s3/operations/get_object", "s3/types/ByteStream"),
        ("streaming", "errors", "lifecycle"),
    ),
    Task(
        "bedrock_input",
        "bedrock-runtime",
        """Implement `async def run(endpoint_url: str, model_id: str,
prompt: str) -> str`. Call converse with one user message containing the prompt
as a text content block, and inference_config max_tokens=32, temperature=0.
Concatenate all text blocks from the returned assistant message, ignoring other
content variants. Return '' for absent output or an unrecognized output
variant. Use region eu-west-2, the supplied endpoint/model ID, and close the
client. Do not serialize the native response with to_dict().""",
        (
            "bedrock-runtime/operations/converse",
            "bedrock-runtime/types/Message",
            "bedrock-runtime/types/ConverseValueOutput",
        ),
        ("inference", "types", "lifecycle"),
    ),
)


def task_named(name: str) -> Task:
    return next(task for task in TASKS if task.name == name)


def read_document(root: Path, relative: str) -> str:
    """Expand the same checked example includes used by the documentation site."""
    text = (root / relative).read_text()
    return re.sub(
        r'--8<-- "([^"]+)"',
        lambda match: (root / match[1]).read_text(),
        text,
    )


def documents(root: Path, task: Task, condition: str) -> list[dict[str, str]]:
    if condition not in {"references", "guides"}:
        raise ValueError("unknown condition")
    paths = [f"docs/reference/{name}.md" for name in task.references]
    if condition == "guides":
        paths += [f"docs/guides/{name}.md" for name in task.guides]
    result = []
    for path in paths:
        content = read_document(root, path)
        result.append(
            {
                "path": path,
                "content": content,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    return result


def prompt(task: Task, version: str, docs: list[dict[str, str]]) -> str:
    return (
        "Write a self-contained solution.py for Python 3.12.15 and rboto "
        + version
        + ". It must pass mypy --strict. Do not use Any, casts to Any, "
        "type: ignore, dynamic getattr, or private rboto APIs. Do not perform I/O "
        "at import time. You have no shell, browser, or filesystem tools. "
        "Return ONLY a JSON object with exactly two keys: "
        "'code' (the complete Python source) and 'dependencies' (a list of pinned "
        "pip requirements for the service extra and Pydantic if used). "
        "Pydantic version is 2.14.3. No third-party libraries besides rboto and "
        "Pydantic are available. All functions and classes must be fully annotated.\n\n"
        + task.prompt
        + "\n\nProvided documentation:\n"
        + "\n\n".join(f"FILE {doc['path']}\n{doc['content']}" for doc in docs)
    )
