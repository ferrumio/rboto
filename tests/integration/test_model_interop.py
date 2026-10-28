"""Validate application models through a compiled S3 client and local HTTP peer."""

import asyncio
import json
import os
import runpy
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from rboto_s3 import S3Client

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
pytestmark = pytest.mark.skipif(bool(SELECTED and "s3" not in SELECTED), reason="S3 not selected")
EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "docs"
BUCKETS = b"""<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<Buckets><Bucket><Name>documents</Name>
<CreationDate>2026-01-02T03:04:05Z</CreationDate></Bucket></Buckets>
</ListAllMyBucketsResult>"""


@dataclass
class Peer:
    endpoint: str = ""
    body: bytes = b"document content"
    buckets: bytes = BUCKETS
    requests: list[str] = field(default_factory=list)


@pytest.fixture
def peer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Peer]:
    # Keep the peer independent of developer credentials, profiles and endpoints.
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    state = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def respond(self, *, head: bool) -> None:
            state.requests.append(self.path)
            listing = self.path.split("?")[0] == "/"
            body = state.buckets if listing else state.body
            self.send_response(200)
            self.send_header("Content-Type", "application/xml" if listing else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_HEAD(self) -> None:
            self.respond(head=True)

        def do_GET(self) -> None:
            self.respond(head=False)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def client(peer: Peer) -> "S3Client":
    from rboto import s3

    return s3(region="us-east-1", endpoint_url=peer.endpoint)


class ObjectMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=True)

    size_bytes: int = Field(validation_alias="content_length", ge=0)
    content_type: str | None = None
    version_id: str | None = None


@pytest.mark.asyncio
async def test_native_response_supports_projection_aliases_and_optional_fields(
    client: "S3Client", peer: Peer
) -> None:
    response = await client.head_object(bucket="documents", key="report.txt")
    model = ObjectMetadata.model_validate(response)

    assert model.size_bytes == len(peer.body)
    assert model.content_type == "text/plain"
    assert model.version_id is None
    assert model == ObjectMetadata.model_validate(response.to_dict())
    assert json.loads(model.model_dump_json()) == {
        "size_bytes": len(peer.body),
        "content_type": "text/plain",
        "version_id": None,
    }
    with pytest.raises(AttributeError):
        setattr(response, "content_length", 0)
    assert response.content_length == len(peer.body)


@pytest.mark.asyncio
async def test_native_nested_responses_and_timestamps(client: "S3Client") -> None:
    class Bucket(BaseModel):
        model_config = ConfigDict(from_attributes=True, strict=True)

        name: str
        creation_date: datetime = Field(strict=False)

    class Page(BaseModel):
        model_config = ConfigDict(from_attributes=True, strict=True)

        buckets: list[Bucket]
        continuation_token: str | None = None

    response = await client.list_buckets()
    page = Page.model_validate(response)
    assert page.buckets[0].name == "documents"
    assert page.buckets[0].creation_date == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert page.continuation_token is None
    page.buckets[0].name = "changed in the application"
    assert response.buckets is not None
    assert response.buckets[0].name == "documents"


@pytest.mark.asyncio
async def test_validation_failure_reports_nested_field(client: "S3Client", peer: Peer) -> None:
    example = runpy.run_path(str(EXAMPLES / "pydantic_nested.py"))
    peer.buckets = BUCKETS.replace(b"<Name>documents</Name>", b"")

    with pytest.raises(ValidationError) as error:
        await example["list_bucket_page"](client)
    assert error.value.errors()[0]["loc"] == ("buckets", 0, "name")
    assert error.value.errors()[0]["type"] == "string_type"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_model", [False, True])
async def test_metadata_validation_preserves_stream_ownership(
    client: "S3Client", peer: Peer, invalid_model: bool
) -> None:
    class InvalidMetadata(BaseModel):
        model_config = ConfigDict(from_attributes=True, strict=True)

        content_length: str

    response = await client.get_object(bucket="documents", key="report.txt")
    if invalid_model:
        with pytest.raises(ValidationError) as error:
            InvalidMetadata.model_validate(response)
        assert error.value.errors()[0]["loc"] == ("content_length",)
    else:
        assert ObjectMetadata.model_validate(response).size_bytes == len(peer.body)

    assert await response.body.read() == peer.body
    assert await response.body.read() == b""


@pytest.mark.asyncio
async def test_documented_pydantic_tool_validates_before_request_and_returns_json(
    client: "S3Client", peer: Peer
) -> None:
    example = runpy.run_path(str(EXAMPLES / "pydantic_tool.py"))
    run_tool = example["run_tool"]
    schema = example["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["key"]["minLength"] == 1
    for arguments in ('{"key": ""}', '{"key": 12}', '{"key": "a", "bucket": "other"}'):
        with pytest.raises(ValidationError):
            await run_tool(client, arguments, bucket="documents")
    assert peer.requests == []

    results = await asyncio.gather(
        run_tool(client, '{"key": "first.txt"}', bucket="documents"),
        run_tool(client, '{"key": "second.txt"}', bucket="documents"),
    )
    assert all(json.loads(result)["content_length"] == len(peer.body) for result in results)
    assert len(peer.requests) == 2


@pytest.mark.asyncio
async def test_documented_nested_projection(client: "S3Client") -> None:
    example = runpy.run_path(str(EXAMPLES / "pydantic_nested.py"))
    result = await example["list_bucket_page"](client)
    assert json.loads(result.model_dump_json()) == {
        "buckets": [{"name": "documents", "creation_date": "2026-01-02T03:04:05Z"}],
        "continuation_token": None,
    }


@pytest.mark.asyncio
async def test_documented_dataclass_projection_and_dictionary_validation(
    client: "S3Client", peer: Peer
) -> None:
    example = runpy.run_path(str(EXAMPLES / "dataclass_tool.py"))
    data = example["DocumentInput"](key="report.txt")
    result = await example["inspect_document"](client, data, bucket="documents")
    assert result.content_length == len(peer.body)
    assert result.content_type == "text/plain"

    response = await client.head_object(bucket="documents", key=data.key)
    adapter = TypeAdapter(example["DocumentOutput"])
    assert adapter.validate_python(response.to_dict()) == result
    assert adapter.validate_json(adapter.dump_json(result)) == result


def test_documented_dataclass_schema_and_argument_validation() -> None:
    example = runpy.run_path(str(EXAMPLES / "dataclass_schema.py"))
    assert example["input_schema"]["properties"]["key"]["minLength"] == 1
    assert example["parse_arguments"]('{"key": "report.txt"}').key == "report.txt"
    for arguments in ('{"key": ""}', '{"key": 12}'):
        with pytest.raises(ValidationError):
            example["parse_arguments"](arguments)


def test_lambda_handler_reuses_client_across_invocations(
    client: "S3Client", peer: Peer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rboto

    # Only redirect client construction; requests use the compiled client and peer.
    monkeypatch.setattr(rboto, "S3Client", lambda: client)
    monkeypatch.setenv("DOCUMENT_BUCKET", "documents")
    example = runpy.run_path(str(EXAMPLES / "lambda_function.py"))
    handler = example["lambda_handler"]

    first = handler({"key": "first.txt"}, None)
    with pytest.raises(ValidationError):
        handler({"key": 123}, None)
    second = handler({"key": "second.txt"}, None)

    assert first == second == {
        "content_length": len(peer.body),
        "content_type": "text/plain",
    }
    assert json.loads(json.dumps(second)) == second
    assert peer.requests == ["/documents/first.txt", "/documents/second.txt"]
