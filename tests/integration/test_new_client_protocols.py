"""Exercise generated conversions through the real SDK and a local HTTP peer."""

import asyncio
import base64
import io
import json
import os
import struct
import threading
import zlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from rboto import Next, OperationRequest, OperationResponse

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and name not in SELECTED), reason="service not selected"
    )


def event_frame(event: str, payload: dict[str, object], *, exception: bool = False) -> bytes:
    headers = bytearray()
    for name, value in {
        ":message-type": "exception" if exception else "event",
        ":exception-type" if exception else ":event-type": event,
        ":content-type": "application/json",
    }.items():
        key, text = name.encode(), value.encode()
        headers += bytes([len(key)]) + key + b"\x07" + struct.pack(">H", len(text)) + text
    body = json.dumps(payload).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    message = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


@dataclass
class Peer:
    endpoint: str = ""
    body: bytes = b"{}"
    status: int = 200
    content_type: str = "application/json"
    duplex: bool = False
    requests: list[tuple[str, bytes]] = field(default_factory=list)
    request_received: threading.Event = field(default_factory=threading.Event)
    response_gate: threading.Event | None = None


@pytest.fixture
def peer(monkeypatch: pytest.MonkeyPatch) -> Iterator[Peer]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    config = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def read_body(self) -> bytes:
            if self.headers.get("Transfer-Encoding") != "chunked":
                return self.rfile.read(int(self.headers.get("Content-Length", "0")))
            chunks = []
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                size = int(line.split(b";")[0], 16)
                if not size:
                    while self.rfile.readline() not in {b"\r\n", b""}:
                        pass
                    break
                chunks.append(self.rfile.read(size))
                assert self.rfile.read(2) == b"\r\n"
            return b"".join(chunks)

        def send_chunk(self, body: bytes) -> None:
            self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
            self.wfile.flush()

        def do_POST(self) -> None:
            if config.duplex:
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.amazon.eventstream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.send_chunk(
                    event_frame("chunk", {"bytes": base64.b64encode(b"ready").decode()})
                )
                config.requests.append((self.path, self.read_body()))
                self.send_chunk(event_frame("chunk", {"bytes": base64.b64encode(b"done").decode()}))
                self.send_chunk(b"")
                return
            config.requests.append((self.path, self.read_body()))
            gate = config.response_gate
            config.request_received.set()
            if gate is not None:
                assert gate.wait(timeout=5)
            self.send_response(config.status)
            self.send_header("Content-Type", config.content_type)
            self.send_header("Content-Length", str(len(config.body)))
            self.end_headers()
            try:
                self.wfile.write(config.body)
            except (BrokenPipeError, ConnectionResetError):
                # A canceled request may close the connection before the peer replies.
                pass

        do_GET = do_POST
        do_PUT = do_POST

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    config.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield config
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
@service("dynamodb")
async def test_query_paginator_forwards_native_keys_and_middleware_per_page(
    peer: Peer,
    json_logging: io.StringIO,
) -> None:
    from rboto import DynamoDBClient, configure_logging
    from rboto_dynamodb import QueryOutput

    observations: list[OperationRequest] = []

    async def middleware(request: OperationRequest, call_next: Next) -> OperationResponse:
        observations.append(request)
        return await call_next(request)

    client = DynamoDBClient(
        region="us-east-1", endpoint_url=peer.endpoint, interceptors=[middleware],
    )
    assert client.paginators is client.paginators
    peer.body = json.dumps({
        "Items": [], "Count": 0,
        "LastEvaluatedKey": {"pk": {"S": "a"}, "sk": {"B": base64.b64encode(b"b").decode()}},
    }).encode()
    configure_logging(stream=json_logging)
    pages = client.paginators.query(table_name="example", limit=1)
    assert peer.requests == []
    first = await anext(pages)
    assert isinstance(first, QueryOutput)
    assert not first.items
    cursor = first.last_evaluated_key
    assert cursor is not None
    cursor.clear()
    peer.body = b'{"Items":[{"pk":{"S":"last"}}],"Count":1}'
    second = await anext(pages)
    assert second.items == [{"pk": {"s": "last"}}]
    with pytest.raises(StopAsyncIteration):
        await anext(pages)
    assert len(peer.requests) == len(observations) == 2
    request = json.loads(peer.requests[1][1])
    assert request["ExclusiveStartKey"] == {"pk": {"S": "a"}, "sk": {"B": "Yg=="}}
    assert request["TableName"] == "example" and request["Limit"] == 1
    assert all(item.metadata.dispatched for item in observations)
    events = [json.loads(line) for line in json_logging.getvalue().splitlines()]
    assert len(events) == 2
    assert all(event["operation"] == "Query" and event["outcome"] == "success"
               for event in events)


@pytest.mark.asyncio
@service("s3")
async def test_s3_paginator_uses_composite_cursor_and_completion_flag(peer: Peer) -> None:
    from rboto import S3Client

    client = S3Client(region="us-east-1", endpoint_url=peer.endpoint)
    peer.content_type = "application/xml"
    peer.body = (
        b"<ListMultipartUploadsResult><IsTruncated>true</IsTruncated>"
        b"<NextKeyMarker>folder/key</NextKeyMarker><NextUploadIdMarker>upload-1</NextUploadIdMarker>"
        b"</ListMultipartUploadsResult>"
    )
    pages = client.paginators.list_multipart_uploads(bucket="example", max_uploads=2)
    await anext(pages)
    peer.body = (
        b"<ListMultipartUploadsResult><IsTruncated>false</IsTruncated>"
        b"<NextKeyMarker>unused</NextKeyMarker><NextUploadIdMarker>unused</NextUploadIdMarker>"
        b"</ListMultipartUploadsResult>"
    )
    await anext(pages)
    with pytest.raises(StopAsyncIteration):
        await anext(pages)
    assert len(peer.requests) == 2
    params = parse_qs(urlsplit(peer.requests[1][0]).query)
    assert params["key-marker"] == ["folder/key"]
    assert params["upload-id-marker"] == ["upload-1"]
    assert params["max-uploads"] == ["2"]


@pytest.mark.asyncio
@service("s3")
async def test_s3_list_objects_paginator_falls_back_to_last_key(peer: Peer) -> None:
    from rboto import S3Client

    client = S3Client(region="us-east-1", endpoint_url=peer.endpoint)
    peer.content_type = "application/xml"
    peer.body = (
        b"<ListBucketResult><IsTruncated>true</IsTruncated>"
        b"<Contents><Key>a</Key></Contents><Contents><Key>folder/b</Key></Contents>"
        b"</ListBucketResult>"
    )
    pages = client.paginators.list_objects(bucket="example")
    await anext(pages)
    peer.body = b"<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>"
    await anext(pages)
    with pytest.raises(StopAsyncIteration):
        await anext(pages)
    assert len(peer.requests) == 2
    assert parse_qs(urlsplit(peer.requests[1][0]).query)["marker"] == ["folder/b"]


@pytest.mark.asyncio
@service("sqs")
async def test_sqs_paginator_forwards_token_and_stops_when_it_repeats(peer: Peer) -> None:
    from rboto import SQSClient

    client = SQSClient(region="us-east-1", endpoint_url=peer.endpoint)
    peer.body = b'{"QueueUrls":[],"NextToken":"same"}'
    pages = [page async for page in client.paginators.list_queues(max_results=10)]
    assert len(pages) == len(peer.requests) == 2
    assert json.loads(peer.requests[1][1]) == {"MaxResults": 10, "NextToken": "same"}


@pytest.mark.asyncio
@service("dynamodb")
async def test_native_outputs_keep_owned_values_and_fresh_mappings(peer: Peer) -> None:
    from rboto import dynamodb

    peer.body = json.dumps(
        {
            "Item": {"pk": {"S": "example"}, "nested": {"L": [{"N": "1"}, {"BOOL": True}]}},
            "ConsumedCapacity": {"TableName": "example", "CapacityUnits": 1.0},
        }
    ).encode()
    client = dynamodb(region="us-east-1", endpoint_url=peer.endpoint)
    outputs = await asyncio.gather(
        *(client.get_item(table_name="example", key={"pk": {"s": "example"}}) for _ in range(8))
    )
    del client
    for output in outputs:
        assert type(output).__name__ == "GetItemOutput"
        expected = {"pk": {"s": "example"}, "nested": {"l": [{"n": "1"}, {"bool": True}]}}
        assert output.item == expected
        item = output.item
        assert item is not None
        item.clear()
        assert output.item == expected
        assert output.to_dict()["item"] == expected
        assert output.consumed_capacity is not None
        assert output.consumed_capacity.capacity_units == 1.0
        with pytest.raises(AttributeError):
            output.item = {}


@pytest.mark.asyncio
@service("dynamodb")
async def test_input_containers_preserve_unsized_iterables(peer: Peer) -> None:
    from rboto import dynamodb

    class Attributes:
        def __iter__(self) -> Iterator[str]:
            return iter(["pk", "nested"])

        def __len__(self) -> int:
            raise AssertionError("input conversion must not require an iterable length")

    peer.body = b'{"Item":{"pk":{"S":"example"}}}'
    client = dynamodb(region="us-east-1", endpoint_url=peer.endpoint)
    for attributes in (["pk", "nested"], Attributes()):
        output = await client._native.get_item(
            {
                "table_name": "example",
                "key": {"pk": {"s": "example"}},
                "attributes_to_get": attributes,
                "expression_attribute_names": {f"#field{i}": f"field{i}" for i in range(64)},
            }
        )
        assert output.item == {"pk": {"s": "example"}}
        request = json.loads(peer.requests[-1][1])
        assert request["AttributesToGet"] == ["pk", "nested"]
        assert len(request["ExpressionAttributeNames"]) == 64


@pytest.mark.asyncio
@service("dynamodb")
async def test_native_request_cancellation_keeps_client_usable(peer: Peer) -> None:
    from rboto import dynamodb

    peer.body = b'{"Item":{"pk":{"S":"example"}}}'
    gate = threading.Event()
    peer.response_gate = gate
    client = dynamodb(region="us-east-1", endpoint_url=peer.endpoint)
    pending = asyncio.create_task(
        client.get_item(table_name="example", key={"pk": {"s": "example"}})
    )
    try:
        async with asyncio.timeout(10):
            assert await asyncio.to_thread(peer.request_received.wait, 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            peer.response_gate = None
            gate.set()
            output = await client.get_item(table_name="example", key={"pk": {"s": "example"}})
            assert output.item == {"pk": {"s": "example"}}
    finally:
        gate.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@service("dynamodb")
async def test_native_request_preserves_modeled_errors(peer: Peer) -> None:
    from rboto import dynamodb
    from rboto_dynamodb.exceptions import ResourceNotFoundError

    peer.status = 400
    peer.body = b'{"__type":"ResourceNotFoundException","message":"missing table"}'
    client = dynamodb(region="us-east-1", endpoint_url=peer.endpoint)
    with pytest.raises(ResourceNotFoundError, match="missing table") as caught:
        await client.get_item(table_name="missing", key={"pk": {"s": "example"}})
    assert caught.value.operation_name == "GetItem"


@pytest.mark.asyncio
@service("bedrock-runtime")
async def test_documents_unions_and_native_outputs(peer: Peer) -> None:
    from rboto import bedrock_runtime

    document = {"nested": [None, True, 42, -7, 0.25, {"value": "hello"}]}
    peer.body = json.dumps(
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "Hello"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
            "metrics": {"latencyMs": 1},
            "additionalModelResponseFields": document,
        }
    ).encode()
    client = bedrock_runtime(region="us-east-1", endpoint_url=peer.endpoint)
    result = await client.converse(
        model_id="example",
        messages=[{"role": "user", "content": [{"text": "hello"}]}],
        additional_model_request_fields=document,
        inference_config={"temperature": 0.5},
    )
    assert result.additional_model_response_fields == document
    assert result.to_dict()["additional_model_response_fields"] == document
    assert result.usage.input_tokens == 2
    with pytest.raises(AttributeError):
        result.usage.input_tokens = 99
    request = json.loads(peer.requests[0][1])
    assert request["additionalModelRequestFields"] == document
    assert request["messages"][0]["content"] == [{"text": "hello"}]
    assert request["inferenceConfig"]["temperature"] == 0.5


@pytest.mark.asyncio
@service("bedrock-runtime")
async def test_invalid_documents_and_multivariant_unions_fail_before_io(peer: Peer) -> None:
    from rboto import bedrock_runtime

    client = bedrock_runtime(region="us-east-1", endpoint_url=peer.endpoint)
    with pytest.raises(ValueError, match="exactly one"):
        await client.converse(
            model_id="example",
            messages=[{"role": "user", "content": [{"text": "x", "tool_use": {}}]}],
        )
    with pytest.raises(ValueError, match="finite"):
        await client.converse(
            model_id="example", additional_model_request_fields={"x": float("nan")}
        )
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    with pytest.raises(ValueError, match="nesting"):
        await client.converse(model_id="example", additional_model_request_fields=recursive)
    assert not peer.requests


@pytest.mark.asyncio
@service("bedrock-runtime")
async def test_nullable_member_inside_response_union(peer: Peer) -> None:
    from rboto import BedrockRuntimeClient

    peer.body = json.dumps({
        "output": {"message": {
            "role": "assistant",
            "content": [{"toolUse": {
                "toolUseId": "call-1", "name": "lookup", "input": {"query": "example"},
            }}],
        }},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1},
    }).encode()
    client = BedrockRuntimeClient(region="us-east-1", endpoint_url=peer.endpoint)
    response = await client.converse(
        model_id="example", messages=[{"role": "user", "content": [{"text": "hello"}]}],
    )
    assert response.output is not None
    tool = response.output["message"]["content"][0]["tool_use"]
    assert tool["type"] is None


@pytest.mark.asyncio
@service("sqs")
async def test_sequence_input_and_unknown_enum_output(peer: Peer) -> None:
    from rboto import SQSClient, UnknownEnumValue

    peer.body = b'{"Attributes":{"VisibilityTimeout":"30","FutureAttribute":"new"}}'
    client = SQSClient(region="us-east-1", endpoint_url=peer.endpoint)
    response = await client.get_queue_attributes(
        queue_url=f"{peer.endpoint}/123456789012/example", attribute_names=("All",),
    )
    assert response.attributes is not None
    assert response.attributes["VisibilityTimeout"] == "30"
    assert response.attributes[UnknownEnumValue("FutureAttribute")] == "new"
    assert all(isinstance(key, str) for key in response.attributes)
    assert len(peer.requests) == 1


@pytest.mark.asyncio
@service("bedrock-runtime")
@pytest.mark.parametrize("observed", [False, True])
async def test_input_and_output_event_streams_advance_concurrently(
    peer: Peer, observed: bool
) -> None:
    from rboto import bedrock_runtime
    from rboto_bedrock_runtime.types import InvokeModelWithBidirectionalStreamInput

    peer.duplex = True
    released = asyncio.Event()

    async def events() -> AsyncIterator[InvokeModelWithBidirectionalStreamInput]:
        yield {"chunk": {"bytes": b"first"}}
        await released.wait()
        yield {"chunk": {"bytes": b"second"}}

    observations: list[OperationRequest] = []

    async def middleware(request: OperationRequest, call_next: Next) -> OperationResponse:
        observations.append(request)
        return await call_next(request)

    client = bedrock_runtime(
        region="us-east-1",
        endpoint_url=peer.endpoint,
        interceptors=[middleware] if observed else (),
    )
    async with asyncio.timeout(15):
        response = await client.invoke_model_with_bidirectional_stream(
            model_id="example", body=events()
        )
        stream = response["body"]
        assert await anext(stream) == {"chunk": {"bytes": b"ready"}}
        waiting = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        released.set()
        assert [event async for event in stream] == [{"chunk": {"bytes": b"done"}}]
    assert base64.b64encode(b"first") in peer.requests[0][1]
    assert base64.b64encode(b"second") in peer.requests[0][1]
    if observed:
        assert len(observations) == 1 and observations[0].metadata.dispatched
        assert observations[-1].metadata.attempt_count == 1


@pytest.mark.asyncio
@service("bedrock-runtime")
@pytest.mark.parametrize("observed", [False, True])
async def test_modeled_event_stream_errors_are_catchable(peer: Peer, observed: bool) -> None:
    from rboto import bedrock_runtime
    from rboto_bedrock_runtime.exceptions import ValidationError

    peer.content_type = "application/vnd.amazon.eventstream"
    peer.body = event_frame("validationException", {"message": "invalid stream"}, exception=True)
    observations: list[OperationRequest] = []

    async def middleware(request: OperationRequest, call_next: Next) -> OperationResponse:
        observations.append(request)
        return await call_next(request)

    client = bedrock_runtime(
        region="us-east-1",
        endpoint_url=peer.endpoint,
        interceptors=[middleware] if observed else (),
    )
    response = await client.invoke_model_with_response_stream(model_id="example", body=b"{}")
    with pytest.raises(ValidationError, match="invalid stream"):
        await anext(response["body"])
    with pytest.raises(StopAsyncIteration):
        await anext(response["body"])
    if observed:
        assert len(observations) == 1 and observations[0].metadata.dispatched


@pytest.mark.asyncio
@service("qbusiness")
async def test_recursive_inputs_and_sparse_native_outputs(peer: Peer) -> None:
    from rboto import qbusiness

    peer.body = json.dumps(
        {
            "systemMessage": "answer",
            "sourceAttributions": [None, {"title": "Example", "snippet": "source"}],
        }
    ).encode()
    client = qbusiness(region="us-east-1", endpoint_url=peer.endpoint)
    result = await client.chat_sync(
        application_id="example",
        user_message="hello",
        attribute_filter={
            "not_filter": {
                "not_filter": {
                    "equals_to": {
                        "name": "kind",
                        "value": {"string_value": "test"},
                    }
                }
            }
        },
    )
    assert result.source_attributions is not None
    assert result.source_attributions[0] is None
    source = result.source_attributions[1]
    assert source is not None and source.title == "Example"
    assert json.loads(peer.requests[0][1])["attributeFilter"]["notFilter"]["notFilter"] == {
        "equalsTo": {"name": "kind", "value": {"stringValue": "test"}},
    }


@pytest.mark.asyncio
@service("secrets-manager")
async def test_new_service_modeled_error(peer: Peer) -> None:
    from rboto import secrets_manager
    from rboto_secrets_manager.exceptions import ResourceNotFoundError

    peer.status = 400
    peer.body = b'{"__type":"ResourceNotFoundException","Message":"missing secret"}'
    client = secrets_manager(region="us-east-1", endpoint_url=peer.endpoint)
    with pytest.raises(ResourceNotFoundError, match="missing secret") as caught:
        await client.get_secret_value(secret_id="missing")
    assert caught.value.operation_name == "GetSecretValue"


@pytest.mark.asyncio
@service("polly")
async def test_byte_streams_keep_independent_ownership(peer: Peer) -> None:
    from rboto import polly

    peer.body = b"audio response"
    peer.content_type = "audio/mpeg"
    client = polly(region="us-east-1", endpoint_url=peer.endpoint)
    first, second = await asyncio.gather(
        client.synthesize_speech(output_format="mp3", text="one", voice_id="Joanna"),
        client.synthesize_speech(output_format="mp3", text="two", voice_id="Joanna"),
    )
    assert first.audio_stream is not None and second.audio_stream is not None
    assert first.audio_stream is first.audio_stream
    assert first.audio_stream is not second.audio_stream
    assert await first.audio_stream.read() == b"audio response"
    assert await first.audio_stream.read() == b""
    assert await second.audio_stream.read() == b"audio response"


@pytest.mark.asyncio
@service("bedrock-agentcore-control")
async def test_unit_union_variants_and_rust_keyword_fields(peer: Peer) -> None:
    from rboto import bedrock_agentcore_control

    client = bedrock_agentcore_control(region="us-east-1", endpoint_url=peer.endpoint)
    await client.create_harness(
        harness_name="example",
        execution_role_arn="arn:aws:iam::123456789012:role/example",
        tools=[
            {
                "type": "agentcore_gateway",
                "config": {
                    "agent_core_gateway": {
                        "gateway_arn": (
                            "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/example"
                        ),
                        "outbound_auth": {"aws_iam": None},
                    },
                },
            }
        ],
    )
    tool = json.loads(peer.requests[0][1])["tools"][0]
    assert tool["type"] == "agentcore_gateway"
    assert tool["config"]["agentCoreGateway"]["outboundAuth"] == {"awsIam": {}}


@pytest.mark.asyncio
@service("cloudwatch-logs")
async def test_logs_batch_rejections_and_large_timestamps(peer: Peer) -> None:
    from rboto import cloudwatch_logs

    peer.body = json.dumps(
        {
            "nextSequenceToken": "next",
            "rejectedLogEventsInfo": {"tooOldLogEventEndIndex": 0},
        }
    ).encode()
    client = cloudwatch_logs(region="us-east-1", endpoint_url=peer.endpoint)
    result = await client.put_log_events(
        log_group_name="example",
        log_stream_name="stream",
        log_events=[{"timestamp": 1792972800000, "message": "olá"}],
    )
    assert result.rejected_log_events_info is not None
    assert result.rejected_log_events_info.too_old_log_event_end_index == 0
    assert result.next_sequence_token == "next"
    with pytest.raises(AttributeError):
        result.rejected_log_events_info.too_old_log_event_end_index = 3
    request = json.loads(peer.requests[0][1])
    assert request["logEvents"] == [{"timestamp": 1792972800000, "message": "olá"}]


@pytest.mark.asyncio
@service("cloudwatch-logs")
async def test_logs_live_tail_events_and_modeled_timeout(peer: Peer) -> None:
    from rboto import cloudwatch_logs
    from rboto_cloudwatch_logs.exceptions import SessionTimeoutError

    peer.content_type = "application/vnd.amazon.eventstream"
    peer.body = b"".join(
        [
            event_frame("sessionStart", {"sessionId": "session-1"}),
            event_frame(
                "sessionUpdate",
                {
                    "sessionMetadata": {"sampled": False},
                    "sessionResults": [{"timestamp": 1792972800000, "message": "live"}],
                },
            ),
            event_frame("SessionTimeoutException", {"message": "session ended"}, exception=True),
        ]
    )
    # The SDK prepends "stream-" to this endpoint; *.localhost stays on loopback.
    endpoint = peer.endpoint.replace("127.0.0.1", "rboto.localhost")
    client = cloudwatch_logs(region="us-east-1", endpoint_url=endpoint)
    response = await client.start_live_tail(
        log_group_identifiers=["arn:aws:logs:us-east-1:123456789012:log-group:example"]
    )
    stream = response["response_stream"]
    assert (await anext(stream))["session_start"]["session_id"] == "session-1"
    update = (await anext(stream))["session_update"]
    assert update["session_metadata"]["sampled"] is False
    assert update["session_results"][0]["message"] == "live"
    with pytest.raises(SessionTimeoutError, match="session ended") as caught:
        await anext(stream)
    assert caught.value.operation_name == "StartLiveTail"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
@service("ssm")
async def test_ssm_parameter_native_outputs_and_decryption_flag(peer: Peer) -> None:
    from rboto import ssm

    peer.body = json.dumps(
        {
            "Parameter": {
                "Name": "/rboto/example",
                "Type": "SecureString",
                "Value": "test-value",
                "Version": 2,
                "LastModifiedDate": 1792972800,
                "ARN": "arn:aws:ssm:us-east-1:123456789012:parameter/rboto/example",
                "DataType": "text",
            }
        }
    ).encode()
    result = await ssm(region="us-east-1", endpoint_url=peer.endpoint).get_parameter(
        name="/rboto/example:2", with_decryption=True
    )
    assert result.parameter is not None
    assert result.parameter.value == "test-value"
    assert result.parameter.version == 2
    assert result.parameter.type == "SecureString"
    assert result.parameter.last_modified_date.startswith("2026-10-26")
    assert result.to_dict()["parameter"]["name"] == "/rboto/example"
    assert json.loads(peer.requests[0][1]) == {
        "Name": "/rboto/example:2",
        "WithDecryption": True,
    }


@pytest.mark.asyncio
@service("kms")
async def test_kms_binary_values_and_encryption_context(peer: Peer) -> None:
    from rboto import kms

    plaintext = b"\x00rboto\xff"
    ciphertext = b"\x00ciphertext\xfe"
    key = "arn:aws:kms:us-east-1:123456789012:key/example"
    peer.body = json.dumps(
        {
            "CiphertextBlob": base64.b64encode(ciphertext).decode(),
            "KeyId": key,
            "EncryptionAlgorithm": "SYMMETRIC_DEFAULT",
        }
    ).encode()
    client = kms(region="us-east-1", endpoint_url=peer.endpoint)
    encrypted = await client.encrypt(
        key_id=key, plaintext=plaintext, encryption_context={"purpose": "rboto"}
    )
    assert encrypted.ciphertext_blob == ciphertext
    request = json.loads(peer.requests[-1][1])
    assert base64.b64decode(request["Plaintext"]) == plaintext
    assert request["EncryptionContext"] == {"purpose": "rboto"}
    peer.body = json.dumps(
        {
            "Plaintext": base64.b64encode(plaintext).decode(),
            "KeyId": key,
            "EncryptionAlgorithm": "SYMMETRIC_DEFAULT",
        }
    ).encode()
    decrypted = await client.decrypt(
        key_id=key, ciphertext_blob=ciphertext, encryption_context={"purpose": "rboto"}
    )
    assert decrypted.plaintext == plaintext
    assert base64.b64decode(json.loads(peer.requests[-1][1])["CiphertextBlob"]) == ciphertext


@pytest.mark.asyncio
@service("sfn")
@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
async def test_sfn_sync_execution_status_and_metadata(peer: Peer, status: str) -> None:
    from rboto import sfn

    arn = "arn:aws:states:us-east-1:123456789012:stateMachine:example"
    peer.body = json.dumps(
        {
            "executionArn": "arn:aws:states:us-east-1:123456789012:execution:example:run",
            "stateMachineArn": arn,
            "status": status,
            "startDate": 1792972800,
            "stopDate": 1792972801,
            "output": '{"ok":true}' if status == "SUCCEEDED" else None,
            "error": "ExampleFailure" if status == "FAILED" else None,
            "billingDetails": {"billedMemoryUsedInMB": 64, "billedDurationInMilliseconds": 1000},
        }
    ).encode()
    # The SDK prepends "sync-" to this endpoint; *.localhost stays on loopback.
    endpoint = peer.endpoint.replace("127.0.0.1", "rboto.localhost")
    result = await sfn(region="us-east-1", endpoint_url=endpoint).start_sync_execution(
        state_machine_arn=arn, name="run", input='{"value":42}'
    )
    assert result.status == status
    assert result.start_date.startswith("2026-10-26")
    assert result.billing_details is not None
    assert result.billing_details.billed_memory_used_in_mb == 64
    if status == "SUCCEEDED":
        assert result.output == '{"ok":true}'
    else:
        assert result.error == "ExampleFailure"
    assert json.loads(peer.requests[0][1])["input"] == '{"value":42}'


@pytest.mark.asyncio
@service("sesv2")
async def test_sesv2_structured_content_and_binary_attachment(peer: Peer) -> None:
    from rboto import sesv2

    attachment = b"\x00attachment\xff"
    peer.body = b'{"MessageId":"message-123"}'
    result = await sesv2(region="us-east-1", endpoint_url=peer.endpoint).send_email(
        from_email_address="sender@example.invalid",
        destination={"to_addresses": ["recipient@example.invalid"]},
        content={
            "simple": {
                "subject": {"data": "Olá", "charset": "UTF-8"},
                "body": {"text": {"data": "Test message", "charset": "UTF-8"}},
                "attachments": [
                    {
                        "raw_content": attachment,
                        "file_name": "example.bin",
                        "content_disposition": "ATTACHMENT",
                    }
                ],
            }
        },
    )
    assert result.message_id == "message-123"
    path, body = peer.requests[0]
    assert path == "/v2/email/outbound-emails"
    content = json.loads(body)["Content"]["Simple"]
    assert content["Subject"]["Data"] == "Olá"
    assert base64.b64decode(content["Attachments"][0]["RawContent"]) == attachment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_name", "method", "params", "error_code", "exception_name", "operation"),
    [
        pytest.param(
            "cloudwatch-logs",
            "get_log_events",
            {"log_group_name": "missing", "log_stream_name": "missing"},
            "ResourceNotFoundException",
            "ResourceNotFoundError",
            "GetLogEvents",
            marks=service("cloudwatch-logs"),
        ),
        pytest.param(
            "ssm",
            "get_parameter",
            {"name": "/missing"},
            "ParameterNotFound",
            "ParameterNotFoundError",
            "GetParameter",
            marks=service("ssm"),
        ),
        pytest.param(
            "kms",
            "decrypt",
            {"ciphertext_blob": b"invalid"},
            "InvalidCiphertextException",
            "InvalidCiphertextError",
            "Decrypt",
            marks=service("kms"),
        ),
        pytest.param(
            "sfn",
            "describe_state_machine",
            {"state_machine_arn": "arn:aws:states:us-east-1:123456789012:stateMachine:missing"},
            "StateMachineDoesNotExist",
            "StateMachineDoesNotExistError",
            "DescribeStateMachine",
            marks=service("sfn"),
        ),
        pytest.param(
            "sesv2",
            "send_email",
            {"content": {"raw": {"data": b"invalid"}}},
            "MessageRejected",
            "MessageRejectedError",
            "SendEmail",
            marks=service("sesv2"),
        ),
    ],
)
async def test_application_service_modeled_errors(
    peer: Peer,
    service_name: str,
    method: str,
    params: dict[str, object],
    error_code: str,
    exception_name: str,
    operation: str,
) -> None:
    import importlib

    import rboto

    peer.status = 400
    peer.body = json.dumps({"__type": error_code, "message": "modeled failure"}).encode()
    package = service_name.replace("-", "_")
    client = getattr(rboto, package)(region="us-east-1", endpoint_url=peer.endpoint)
    exceptions = importlib.import_module(f"rboto_{package}.exceptions")
    with pytest.raises(getattr(exceptions, exception_name)) as caught:
        await getattr(client, method)(**params)
    assert caught.value.error_code == error_code
    assert caught.value.operation_name == operation


@pytest.mark.asyncio
@service("eventbridge")
async def test_eventbridge_partial_publication_failures(peer: Peer) -> None:
    from rboto import eventbridge

    peer.body = json.dumps(
        {
            "FailedEntryCount": 1,
            "Entries": [
                {"EventId": "event-123"},
                {"ErrorCode": "MalformedDetail", "ErrorMessage": "Detail is not JSON"},
            ],
        }
    ).encode()
    result = await eventbridge(region="us-east-1", endpoint_url=peer.endpoint).put_events(
        entries=[
            {
                "event_bus_name": "example",
                "source": "rboto.test",
                "detail_type": "Example",
                "detail": '{"value":42}',
                "time": "2026-10-26T00:00:00Z",
            },
            {
                "event_bus_name": "example",
                "source": "rboto.test",
                "detail_type": "Example",
                "detail": "invalid JSON",
            },
        ]
    )
    assert result.failed_entry_count == 1
    assert result.entries is not None
    assert result.entries[0].event_id == "event-123"
    assert result.entries[1].error_code == "MalformedDetail"
    assert result.entries[1].error_message == "Detail is not JSON"
    with pytest.raises(AttributeError):
        result.entries[0].event_id = "changed"
    request = json.loads(peer.requests[0][1])
    assert request["Entries"][0]["Detail"] == '{"value":42}'
    assert isinstance(request["Entries"][0]["Time"], (int, float))
    assert request["Entries"][1]["Detail"] == "invalid JSON"


@pytest.mark.asyncio
@service("scheduler")
async def test_scheduler_nested_target_and_timezone(peer: Peer) -> None:
    from rboto import scheduler

    arn = "arn:aws:scheduler:us-east-1:123456789012:schedule/default/example"
    peer.body = json.dumps({"ScheduleArn": arn}).encode()
    result = await scheduler(region="us-east-1", endpoint_url=peer.endpoint).create_schedule(
        name="example",
        schedule_expression="cron(0 9 * * ? *)",
        schedule_expression_timezone="America/Sao_Paulo",
        start_date="2026-10-26T00:00:00Z",
        flexible_time_window={"mode": "OFF"},
        target={
            "arn": "arn:aws:sqs:us-east-1:123456789012:example",
            "role_arn": "arn:aws:iam::123456789012:role/example",
            "input": '{"value":"scheduled"}',
            "retry_policy": {"maximum_retry_attempts": 0},
        },
        action_after_completion="DELETE",
    )
    assert result.schedule_arn == arn
    path, body = peer.requests[0]
    assert path == "/schedules/example"
    request = json.loads(body)
    assert request["ScheduleExpressionTimezone"] == "America/Sao_Paulo"
    assert request["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert request["Target"]["RetryPolicy"]["MaximumRetryAttempts"] == 0
    assert request["Target"]["Input"] == '{"value":"scheduled"}'
    assert isinstance(request["StartDate"], (int, float))
    assert request["ActionAfterCompletion"] == "DELETE"


@pytest.mark.asyncio
@service("pipes")
async def test_pipes_source_filter_and_native_timestamps(peer: Peer) -> None:
    from rboto import pipes

    peer.body = json.dumps(
        {
            "Arn": "arn:aws:pipes:us-east-1:123456789012:pipe/example",
            "Name": "example",
            "DesiredState": "RUNNING",
            "CurrentState": "CREATING",
            "CreationTime": 1792972800,
        }
    ).encode()
    result = await pipes(region="us-east-1", endpoint_url=peer.endpoint).create_pipe(
        name="example",
        source="arn:aws:sqs:us-east-1:123456789012:source",
        target="arn:aws:sqs:us-east-1:123456789012:target",
        role_arn="arn:aws:iam::123456789012:role/example",
        desired_state="RUNNING",
        source_parameters={
            "sqs_queue_parameters": {"batch_size": 1, "maximum_batching_window_in_seconds": 0},
            "filter_criteria": {"filters": [{"pattern": '{"body":{"kind":["example"]}}'}]},
        },
        target_parameters={"input_template": '{"kind":"<$.body.kind>"}'},
        tags={"suite": "bridge"},
    )
    assert result.name == "example"
    assert result.current_state == "CREATING"
    assert result.desired_state == "RUNNING"
    assert result.creation_time is not None and result.creation_time.startswith("2026-10-26")
    assert result.to_dict()["current_state"] == "CREATING"
    path, body = peer.requests[0]
    assert path == "/v1/pipes/example"
    request = json.loads(body)
    assert request["SourceParameters"]["SqsQueueParameters"]["BatchSize"] == 1
    assert request["SourceParameters"]["FilterCriteria"]["Filters"] == [
        {"Pattern": '{"body":{"kind":["example"]}}'}
    ]
    assert request["TargetParameters"]["InputTemplate"] == '{"kind":"<$.body.kind>"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_name",
    [pytest.param(name, marks=service(name)) for name in ("eventbridge", "scheduler", "pipes")],
)
async def test_event_family_modeled_missing_resources(peer: Peer, service_name: str) -> None:
    import importlib

    import rboto

    peer.status = 404
    error_code = "NotFoundException" if service_name == "pipes" else "ResourceNotFoundException"
    peer.body = json.dumps({"__type": error_code, "message": "missing resource"}).encode()
    exceptions = importlib.import_module(f"rboto_{service_name}.exceptions")
    client = getattr(rboto, service_name)(region="us-east-1", endpoint_url=peer.endpoint)
    operation, operation_name = {
        "eventbridge": ("describe_event_bus", "DescribeEventBus"),
        "scheduler": ("get_schedule", "GetSchedule"),
        "pipes": ("describe_pipe", "DescribePipe"),
    }[service_name]
    exception_name = "NotFoundError" if service_name == "pipes" else "ResourceNotFoundError"
    with pytest.raises(getattr(exceptions, exception_name)) as caught:
        await getattr(client, operation)(name="missing")
    assert caught.value.error_code == error_code
    assert caught.value.operation_name == operation_name
