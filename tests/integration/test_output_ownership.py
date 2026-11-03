"""Immutability and child lifetimes through real compiled response bindings."""

from __future__ import annotations

import gc
import json
import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from rboto import ClientConfig
    from rboto_sqs.types import MessageOutput

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
PAYLOAD = "a" * (1024 * 1024)


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if "absent" in self.path:
                body = b"{}"
            elif "empty" in self.path:
                body = b'{"Messages": []}'
            elif "sqs" in self.path:
                body = json.dumps({"Messages": [{
                    "MessageId": str(index), "Body": PAYLOAD,
                    "Attributes": {"FutureAttribute": "future"},
                    "MessageAttributes": {"tag": {
                        "DataType": "String", "StringValue": "value",
                        "StringListValues": ["first", "second"],
                        "BinaryListValues": ["AAH/"],
                    }},
                } for index in range(10)]}).encode()
            else:
                body = json.dumps({"Items": [{
                    "pk": {"S": "key"}, "nested": {"M": {
                        "values": {"L": [{"B": "AAH/"}, {"S": PAYLOAD}, {"NULL": True}]}}},
                }], "ConsumedCapacity": {"TableName": "table", "CapacityUnits": 1}}).encode()
            self.respond(body, "application/x-amz-json-1.0")

        def do_GET(self) -> None:
            if "lambda" in self.path:
                body = json.dumps({"Functions": [
                    {"FunctionName": "first", "Environment": {"Variables": {"KEY": "value"}},
                     "Layers": [{"Arn": "arn:example", "CodeSize": 10}]},
                    {"FunctionName": "second"},
                ]}).encode()
                self.respond(body, "application/json")
            else:
                body = b"""<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
                <Contents><Key>first</Key><Owner><ID>owner</ID></Owner></Contents>
                <Contents><Key>second</Key></Contents></ListBucketResult>"""
                self.respond(body, "application/xml")

        def respond(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def config() -> ClientConfig:
    from rboto import ClientConfig
    return ClientConfig(access_key_id="testing", secret_access_key="testing", max_attempts=1)


@pytest.mark.asyncio
@pytest.mark.skipif(bool(SELECTED and "sqs" not in SELECTED), reason="SQS not selected")
async def test_collection_and_map_containers_are_independent_and_children_outlive_parents(
    endpoint: str,
) -> None:
    from rboto import SQSClient, UnknownEnumValue

    client = SQSClient(region="us-east-1", endpoint_url=endpoint + "/sqs", config=config())
    response = await client.receive_message(queue_url=endpoint + "/sqs/queue")
    messages = response.messages
    assert messages is not None
    again = response.messages
    assert again is not None and again is not messages
    assert len(messages) == len(again) == 10
    child = messages.pop(0)
    again.clear()
    assert response.messages is not None and len(response.messages) == 10
    assert child.receipt_handle is None
    assert child.attributes == {UnknownEnumValue("FutureAttribute"): "future"}
    mapping = child.message_attributes
    assert mapping is not None
    attribute = mapping.pop("tag")
    assert child.message_attributes is not None and "tag" in child.message_attributes
    values = attribute.string_list_values
    assert values is not None
    values.clear()
    assert attribute.string_list_values == ["first", "second"]
    assert attribute.binary_list_values == [b"\x00\x01\xff"]
    snapshot = response.to_dict()
    snapshot.clear()
    del response, messages, again, mapping
    gc.collect()
    assert child.body == PAYLOAD
    assert attribute.string_value == "value"
    with pytest.raises(AttributeError):
        setattr(child, "body", "changed")

    def read_child(value: MessageOutput) -> bool:
        return value.body == PAYLOAD and value.to_dict()["message_id"] == "0"

    # Concurrent getters share immutable Rust data across Python threads.
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(read_child, [child] * 24))
    del child
    gc.collect()
    assert attribute.to_dict()["string_list_values"] == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["absent", "empty"])
@pytest.mark.skipif(bool(SELECTED and "sqs" not in SELECTED), reason="SQS not selected")
async def test_optional_collections_preserve_absence_and_empty(
    endpoint: str, kind: str,
) -> None:
    from rboto import SQSClient

    client = SQSClient(region="us-east-1", endpoint_url=endpoint + f"/sqs/{kind}", config=config())
    response = await client.receive_message(queue_url=endpoint + f"/sqs/{kind}/queue")
    if kind == "absent":
        assert response.messages is None and response.to_dict()["messages"] is None
    else:
        assert response.messages == [] and response.to_dict()["messages"] == []


@pytest.mark.asyncio
@pytest.mark.skipif(bool(SELECTED and "s3" not in SELECTED), reason="S3 not selected")
async def test_nested_optional_structure_survives_both_ancestors(endpoint: str) -> None:
    from rboto import S3Client

    client = S3Client(region="us-east-1", endpoint_url=endpoint + "/s3", config=config())
    response = await client.list_objects_v2(bucket="bucket")
    contents = response.contents
    assert contents is not None
    first, second = contents
    assert second.owner is None
    owner = first.owner
    assert owner is not None
    assert first.to_dict()["owner"] == {"display_name": None, "id": "owner"}
    del first, second, contents, response
    gc.collect()
    assert owner.id == "owner"
    assert owner.to_dict() == {"display_name": None, "id": "owner"}


@pytest.mark.asyncio
@pytest.mark.skipif(bool(SELECTED and "lambda" not in SELECTED), reason="Lambda not selected")
async def test_nested_map_remains_private_to_each_python_access(endpoint: str) -> None:
    from rboto import LambdaClient

    client = LambdaClient(region="us-east-1", endpoint_url=endpoint + "/lambda", config=config())
    response = await client.list_functions()
    functions = response.functions
    assert functions is not None
    first, second = functions
    assert second.environment is None
    environment = first.environment
    assert environment is not None
    variables = environment.variables
    assert variables is not None
    variables["KEY"] = "changed"
    assert environment.variables == {"KEY": "value"}
    assert first.layers is not None and first.layers[0].code_size == 10
    del response, functions, first, second
    gc.collect()
    assert environment.to_dict()["variables"] == {"KEY": "value"}


@pytest.mark.asyncio
@pytest.mark.skipif(bool(SELECTED and "dynamodb" not in SELECTED), reason="DynamoDB not selected")
async def test_dictionary_unions_and_native_structures_preserve_content(endpoint: str) -> None:
    from rboto import DynamoDBClient

    client = DynamoDBClient(
        region="us-east-1", endpoint_url=endpoint + "/dynamodb", config=config(),
    )
    response = await client.scan(table_name="table", return_consumed_capacity="TOTAL")
    items = response.items
    assert items is not None and items[0]["pk"] == {"s": "key"}
    nested = items[0]["nested"]
    assert "m" in nested and "l" in nested["m"]["values"]
    assert nested["m"]["values"]["l"] == [{"b": b"\x00\x01\xff"}, {"s": PAYLOAD}, {"null": True}]
    items[0].clear()
    assert response.items is not None and response.items[0]["pk"] == {"s": "key"}
    capacity = response.consumed_capacity
    assert capacity is not None
    assert response.to_dict()["consumed_capacity"] == capacity.to_dict()
    del response, items, nested
    gc.collect()
    assert capacity.table_name == "table" and capacity.capacity_units == 1
