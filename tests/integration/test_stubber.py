"""Application stubs exercise the compiled Output constructors without services."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from rboto import Next, OperationMetadata, OperationRequest, OperationResponse
from rboto.testing import (
    InvalidFixtureError,
    Stubber,
    StubMismatchError,
    UnconsumedStubsError,
)

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and name not in SELECTED), reason="service not selected",
    )


@service("dynamodb")
@pytest.mark.asyncio
async def test_native_identity_nested_fields_immutability_and_snapshot() -> None:
    from rboto import DynamoDBClient
    from rboto_dynamodb.types import GetItemOutput

    client = DynamoDBClient()
    fixture: dict[str, object] = {
        "item": {"pk": {"s": "one"}, "values": {"l": [{"b": b"\x00\xff"}, {"null": True}]}},
        "consumed_capacity": {"table_name": "items", "capacity_units": 1.5},
    }
    with Stubber(client) as stub:
        stub.add_response(
            client.get_item, fixture, table_name="items", key={"pk": {"s": "one"}},
        )
        fixture.clear()
        output = await client.get_item(table_name="items", key={"pk": {"s": "one"}})
        assert type(output) is GetItemOutput
        assert output.item == {
            "pk": {"s": "one"}, "values": {"l": [{"b": b"\x00\xff"}, {"null": True}]},
        }
        child = output.consumed_capacity
        assert child is not None
        assert child.table_name == "items" and child.capacity_units == 1.5
        assert child.read_capacity_units is None
        assert output.to_dict()["consumed_capacity"] == child.to_dict()
        with pytest.raises(AttributeError):
            setattr(child, "table_name", "changed")
        assert output.item is not None
        output.item.clear()
        assert output.item
        del output
        assert child.table_name == "items"


@service("dynamodb")
@pytest.mark.parametrize("fixture", [
    {"typo": True},
    {"item": {"pk": {"s": "one", "n": "1"}}},
    {"item": {"pk": {"unknown": True}}},
    {"item": {"pk": {"s": 1}}},
    {"item": {"pk": {"b": [1, 2]}}},
    {"item": {"pk": {"null": 1}}},
    {"consumed_capacity": {"typo": True}},
    {"consumed_capacity": {"capacity_units": True}},
])
def test_invalid_response_fixtures_fail_during_registration(fixture: dict[str, object]) -> None:
    from rboto import DynamoDBClient

    client = DynamoDBClient()
    stub = Stubber(client)
    with pytest.raises(InvalidFixtureError):
        stub.add_response(client.get_item, fixture, table_name="items", key={"pk": {"s": "one"}})
    stub.assert_no_pending_responses()


@service("dynamodb")
@pytest.mark.asyncio
async def test_matching_is_independent_of_distinct_call_scheduling_and_client() -> None:
    from rboto import DynamoDBClient

    first, second = DynamoDBClient(), DynamoDBClient()
    with Stubber(first) as one, Stubber(second) as two:
        for index in range(12):
            one.add_response(first.list_tables, {"table_names": [str(index)]}, limit=index + 1)
        two.add_response(second.list_tables, {"table_names": ["other"]}, limit=1)
        results = await asyncio.gather(*(
            first.list_tables(limit=index + 1) for index in reversed(range(12))
        ), second.list_tables(limit=1))
        assert [result.table_names for result in results] == [
            *[[str(index)] for index in reversed(range(12))], ["other"],
        ]
        one.add_response(first.list_tables, {"table_names": ["first"]})
        one.add_response(first.list_tables, {"table_names": ["second"]})
        assert (await first.list_tables()).table_names == ["first"]
        assert (await first.list_tables()).table_names == ["second"]


@service("dynamodb")
@pytest.mark.asyncio
async def test_model_errors_observers_and_zero_native_metadata() -> None:
    from rboto import DynamoDBClient
    from rboto_dynamodb.exceptions import ResourceNotFoundError

    observations: list[OperationMetadata] = []

    async def observe(request: OperationRequest, next_call: Next) -> OperationResponse:
        try:
            return await next_call(request)
        finally:
            observations.append(request.metadata)

    class ApplicationClient(DynamoDBClient):
        pass

    client = ApplicationClient(interceptors=[observe])
    original = client._interceptors
    error = ResourceNotFoundError("missing", error_code="ResourceNotFoundException")
    with Stubber(client) as stub:
        stub.add_response(client.list_tables, {"table_names": []})
        stub.add_error(client.describe_table, error, table_name="missing")
        await client.list_tables()
        with pytest.raises(ResourceNotFoundError) as raised:
            await client.describe_table(table_name="missing")
        assert raised.value is not error
        assert raised.value.operation_name == "DescribeTable"
        assert raised.value.error_code == "ResourceNotFoundException"
    assert client._interceptors is original
    assert len(observations) == 2
    assert all(not item.dispatched and item.attempt_count == item.retry_count == 0
               and item.attempts == () for item in observations)
    assert error.operation_name == "UnknownOperation"


@service("dynamodb")
@pytest.mark.asyncio
async def test_mismatch_does_not_consume_or_expose_payloads() -> None:
    from rboto import DynamoDBClient

    client = DynamoDBClient()
    with Stubber(client) as stub:
        stub.add_response(client.describe_table, {}, table_name="private-table")
        with pytest.raises(StubMismatchError, match="table_name") as error:
            await client.describe_table(table_name="another-private-table")
        assert "private-table" not in str(error.value)
        with pytest.raises(StubMismatchError, match="unexpected call"):
            await client.list_tables()
        await client.describe_table(table_name="private-table")


@service("dynamodb")
def test_unconsumed_exception_cleanup_and_client_method_validation() -> None:
    from rboto import DynamoDBClient

    client, other = DynamoDBClient(), DynamoDBClient()
    original = client._interceptors
    with pytest.raises(UnconsumedStubsError, match="ListTables"):
        with Stubber(client) as stub:
            stub.add_response(client.list_tables, {})
    assert client._interceptors is original
    with pytest.raises(RuntimeError, match="application failed"):
        with Stubber(client) as stub:
            stub.add_response(client.list_tables, {})
            raise RuntimeError("application failed")
    assert client._interceptors is original
    with Stubber(client) as stub:
        with pytest.raises(InvalidFixtureError, match="bound operation"):
            stub.add_response(other.list_tables, {})
        with pytest.raises(RuntimeError, match="already has an active"):
            with Stubber(client):
                pass


@service("dynamodb")
@pytest.mark.asyncio
async def test_expected_parameters_are_typed_and_sequences_are_normalized() -> None:
    from rboto import DynamoDBClient

    client = DynamoDBClient()
    with Stubber(client) as stub:
        stub.add_response(
            client.put_item, {}, table_name="items", item={"tags": {"ss": ("a", "b")}},
        )
        await client.put_item(table_name="items", item={"tags": {"ss": ["a", "b"]}})
        for parameters in (
            {"table_name": "items", "key": {"pk": {"unknown": True}}},
            {"table_name": "items", "key": {"pk": {"s": "a", "n": "1"}}},
            {"table_name": "items", "key": {"pk": {"s": 12}}},
            {"table_name": None, "key": {}},
            {"table_name": "items", "key": {}, "consistent_read": 1},
        ):
            with pytest.raises(InvalidFixtureError):
                stub.add_response(client.get_item, {}, **parameters)


@service("s3")
@pytest.mark.asyncio
async def test_fresh_byte_streams_and_native_stream_reuse_rejection() -> None:
    from rboto import S3Client
    from rboto_s3.types import GetObjectOutput

    client = S3Client()
    with Stubber(client) as stub:
        for _ in range(2):
            stub.add_response(
                client.get_object, {"body": b"\x00fixture\xff"}, bucket="bucket", key="key",
            )
        first, second = await asyncio.gather(*(
            client.get_object(bucket="bucket", key="key") for _ in range(2)
        ))
        assert type(first) is GetObjectOutput
        assert first.body is not second.body
        assert await first.body.read() == b"\x00fixture\xff"
        assert await second.body.read() == b"\x00fixture\xff"
        assert await first.body.read() == b""
        with pytest.raises(InvalidFixtureError, match="existing stream"):
            stub.add_response(client.get_object, first, bucket="bucket", key="key")
        with pytest.raises(InvalidFixtureError, match="requires field 'body'"):
            stub.add_response(client.get_object, {}, bucket="bucket", key="key")


@service("lambda")
@pytest.mark.asyncio
async def test_known_and_future_enum_responses_and_nullable_fields() -> None:
    from rboto import LambdaClient, UnknownEnumValue
    from rboto_lambda.types import GetFunctionConfigurationOutput

    client = LambdaClient()
    with Stubber(client) as stub:
        stub.add_response(
            client.get_function_configuration,
            {
                "runtime": "future-runtime", "state": "Active", "code_size": 0,
                "layers": [{"arn": "example", "code_size": 42}],
            },
            function_name="example",
        )
        output = await client.get_function_configuration(function_name="example")
        assert type(output) is GetFunctionConfigurationOutput
        assert output.runtime == UnknownEnumValue("future-runtime")
        assert output.state == "Active"
        assert output.function_name is None
        assert output.layers is not None and output.layers[0].code_size == 42
        assert output.layers[0].signing_job_arn is None
        for fixture in ({}, {"code_size": None}, {"code_size": 0, "layers": [{"arn": "example"}]}):
            with pytest.raises(InvalidFixtureError):
                stub.add_response(client.get_function_configuration, fixture, function_name="bad")
        stub.add_response(client.get_function_configuration, output, function_name="again")
        assert await client.get_function_configuration(function_name="again") is output
        with pytest.raises(InvalidFixtureError, match="event-stream"):
            stub.add_response(client.invoke_with_response_stream, {}, function_name="example")


@service("bedrock-runtime")
@pytest.mark.asyncio
async def test_nested_output_union_required_json_null_and_future_enum() -> None:
    from rboto import BedrockRuntimeClient
    from rboto_bedrock_runtime.types import ConverseOutput

    client = BedrockRuntimeClient()
    fixture: dict[str, object] = {
        "stop_reason": "future-stop",
        "output": {"message": {"role": "assistant", "content": [
            {"tool_use": {"tool_use_id": "one", "name": "lookup", "input": None}},
        ]}},
        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        "metrics": {"latency_ms": 5},
        "additional_model_response_fields": {"nullable": None, "items": [True, 1, "text"]},
    }
    with Stubber(client) as stub:
        stub.add_response(client.converse, fixture, model_id="model")
        output = await client.converse(model_id="model")
        assert type(output) is ConverseOutput
        assert output.stop_reason == "future-stop"
        assert output.output == {"message": {"role": "assistant", "content": [
            {"tool_use": {"tool_use_id": "one", "name": "lookup", "input": None, "type": None}},
        ]}}
        assert output.usage is not None and output.usage.total_tokens == 3
        assert output.metrics is not None and output.metrics.latency_ms == 5
        assert output.additional_model_response_fields == {
            "nullable": None, "items": [True, 1, "text"],
        }
        with pytest.raises(InvalidFixtureError, match="requires field 'input'"):
            stub.add_response(client.converse, {
                "stop_reason": "tool_use",
                "output": {"message": {"role": "assistant", "content": [
                    {"tool_use": {"tool_use_id": "one", "name": "lookup"}},
                ]}},
            }, model_id="model")


@service("dynamodb")
def test_no_native_runtime_credentials_or_network_in_fresh_process(tmp_path: Path) -> None:
    marker = tmp_path / "credential-provider-called"
    config = tmp_path / "config"
    config.write_text(
        '[profile fixture]\nregion=eu-west-2\ncredential_process = '
        f'{sys.executable} -c "from pathlib import Path; Path({str(marker)!r}).touch()"\n'
    )
    script = """
import asyncio
from rboto import DynamoDBClient
from rboto.testing import Stubber, StubMismatchError
from rboto_dynamodb._native import _runtime_worker_threads
assert _runtime_worker_threads() is None
client = DynamoDBClient(profile="fixture", endpoint_url="http://127.0.0.1:1")
async def test():
    with Stubber(client) as stub:
        stub.add_response(client.list_tables, {"table_names": ["local"]})
        assert (await client.list_tables()).table_names == ["local"]
        try:
            await client.list_tables()
        except StubMismatchError:
            pass
        else:
            raise AssertionError("unexpected request accepted")
asyncio.run(test())
assert _runtime_worker_threads() is None
"""
    env = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30,
        env={**env, "AWS_CONFIG_FILE": str(config),
             "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "credentials"),
             "AWS_EC2_METADATA_DISABLED": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


@pytest.fixture
def unknown_union_peer() -> Iterator[str]:
    """Only obtain a native forward-compatible value; the stub itself does no I/O."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps({"Item": {"future": {"FutureVariant": "opaque"}}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/x-amz-json-1.0")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@service("dynamodb")
@pytest.mark.asyncio
async def test_existing_native_unknown_union_is_preserved(unknown_union_peer: str) -> None:
    from rboto import ClientConfig, DynamoDBClient

    live = DynamoDBClient(
        region="eu-west-2", endpoint_url=unknown_union_peer,
        config=ClientConfig(access_key_id="testing", secret_access_key="testing", max_attempts=1),
    )
    native = await live.get_item(table_name="items", key={"pk": {"s": "one"}})
    assert native.item == {"future": {"unknown": True}}
    client = DynamoDBClient(endpoint_url="http://127.0.0.1:1")
    with Stubber(client) as stub:
        stub.add_response(client.get_item, native, table_name="items", key={"pk": {"s": "one"}})
        output = await client.get_item(table_name="items", key={"pk": {"s": "one"}})
        assert output is native
        assert output.to_dict()["item"] == {"future": {"unknown": True}}
