"""Conversion paths through compiled adapters; malformed inputs never send."""

from __future__ import annotations

import asyncio
import importlib
import os
import socket
from collections.abc import Iterator

import pytest
from rboto.exceptions import ConversionErrorContext, get_conversion_context

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and name not in SELECTED), reason="service not selected",
    )


def native_client(service_id: str, class_name: str) -> object:
    module = importlib.import_module(f"rboto_{service_id.replace('-', '_')}._native")
    return getattr(module, class_name)(region="eu-west-2", endpoint_url="http://127.0.0.1:1")


def conversion_failure(
    service_id: str, class_name: str, method: str, parameters: dict[str, object],
    error_type: type[Exception], path: str, category: str,
) -> BaseException:
    # Synchronous preparation must fail before the bridge even needs an event loop.
    client = native_client(service_id, class_name)
    with pytest.raises(error_type) as caught:
        getattr(client, method)(parameters)
    context = get_conversion_context(caught.value)
    assert context is not None
    assert context.service == service_id
    assert context.path == path
    assert context.category == category
    assert context.expected
    assert "CANARY" not in str(caught.value) + repr(context)
    assert not hasattr(caught.value, "_rboto_input_detail")
    return caught.value


@service("bedrock-runtime")
@pytest.mark.parametrize(
    ("messages", "error_type", "path", "category"),
    [
        ([{"role": "user", "content": [{"text": "CANARY"}, {"text": 7}]}],
         TypeError, "messages[0].content[1].text", "type"),
        ([{"role": "user", "content": [{"text": "CANARY"}, {}]}],
         ValueError, "messages[0].content[1]", "union"),
        ([{"role": "user", "content": [{"text": "CANARY", "image": {}}]}],
         ValueError, "messages[0].content[0]", "union"),
        ([{"role": "user", "content": [{"CANARY": "private"}]}],
         ValueError, "messages[0].content[0]", "union"),
        ([{"content": [{"text": "CANARY"}]}],
         ValueError, "messages[0].role", "missing"),
        ([{"role": "user", "content": [{"document": {}}]}],
         ValueError, "messages[0].content[0].document.name", "missing"),
        (["CANARY"], TypeError, "messages[0]", "type"),
    ],
)
def test_nested_structures_and_unions(
    messages: object, error_type: type[Exception], path: str, category: str,
) -> None:
    error = conversion_failure(
        "bedrock-runtime", "BedrockRuntimeClient", "converse",
        {"model_id": "CANARY", "messages": messages}, error_type, path, category,
    )
    context = get_conversion_context(error)
    assert context is not None and context.operation == "Converse"


@service("dynamodb")
@pytest.mark.parametrize(
    ("item", "error_type", "path", "category"),
    [
        ({"CANARY": {"m": {"CANARY": {"l": [{"s": "ok"}, {"s": 7}]}}}},
         TypeError, "item[value#0].m[value#0].l[1].s", "type"),
        ({7: {"s": "CANARY"}}, TypeError, "item[key#0]", "type"),
        ({"CANARY": "CANARY"}, TypeError, "item[value#0]", "type"),
        ({"CANARY": {}}, ValueError, "item[value#0]", "union"),
        ({"CANARY": {"b": [256]}}, OverflowError, "item[value#0].b", "overflow"),
    ],
)
def test_map_positions_redact_arbitrary_keys(
    item: object, error_type: type[Exception], path: str, category: str,
) -> None:
    conversion_failure(
        "dynamodb", "DynamoDBClient", "put_item", {"table_name": "CANARY", "item": item},
        error_type, path, category,
    )


@service("dynamodb")
def test_overflow_keeps_its_exception_class() -> None:
    conversion_failure(
        "dynamodb", "DynamoDBClient", "list_tables", {"limit": 2**100},
        OverflowError, "limit", "overflow",
    )


@service("s3")
def test_timestamp_does_not_echo_invalid_string() -> None:
    conversion_failure(
        "s3", "S3Client", "get_object",
        {"bucket": "CANARY", "key": "CANARY", "if_modified_since": "invalid-CANARY"},
        ValueError, "if_modified_since", "value",
    )

@service("s3")
def test_nested_keyword_uses_the_dictionary_key_without_an_underscore() -> None:
    conversion_failure(
        "s3", "S3Client", "put_bucket_lifecycle_configuration",
        {"bucket": "CANARY", "lifecycle_configuration": {
            "rules": [{"filter": {"and": {"prefix": 7}}}],
        }},
        TypeError, "lifecycle_configuration.rules[0].filter.and.prefix", "type",
    )


@service("bedrock-runtime")
@pytest.mark.parametrize(
    ("document", "error_type", "path", "category"),
    [
        ({"CANARY": [object()]}, TypeError, "[value#0][0]", "type"),
        ({"CANARY": [float("nan")]}, ValueError, "[value#0][0]", "value"),
        ({"CANARY": [2**100]}, OverflowError, "[value#0][0]", "overflow"),
        ({7: "CANARY"}, TypeError, "[key#0]", "type"),
    ],
)
def test_document_conversion(
    document: object, error_type: type[Exception], path: str, category: str,
) -> None:
    conversion_failure(
        "bedrock-runtime", "BedrockRuntimeClient", "converse",
        {"model_id": "CANARY", "additional_model_request_fields": document},
        error_type, "additional_model_request_fields" + path, category,
    )


@service("bedrock-runtime")
def test_deep_document_has_bounded_context() -> None:
    document: object = object()
    for _ in range(140):
        document = {"CANARY": document}
    client = native_client("bedrock-runtime", "BedrockRuntimeClient")
    with pytest.raises(ValueError) as caught:
        getattr(client, "converse")(
            {"model_id": "CANARY", "additional_model_request_fields": document},
        )
    context = get_conversion_context(caught.value)
    assert context is not None and context.truncated
    assert len(context.path) == 512
    assert context.path.startswith("additional_model_request_fields[value#0]")
    assert "CANARY" not in str(caught.value) + repr(context)
    assert len(str(caught.value)) < 1000


@service("dynamodb")
def test_python_scalar_exception_preserves_identity_and_cause() -> None:
    class ApplicationError(ValueError):
        pass

    error = ApplicationError("application detail")
    cause = LookupError("application cause")

    class Index:
        def __index__(self) -> int:
            raise error from cause

    caught = conversion_failure(
        "dynamodb", "DynamoDBClient", "list_tables", {"limit": Index()},
        ApplicationError, "limit", "value",
    )
    assert caught is error and caught.__cause__ is cause
    assert str(caught) == "application detail"
    assert caught.__traceback__ is not None
    assert "limit: expected int" in caught.__notes__[-1]


@service("dynamodb")
def test_iterator_exception_identifies_next_index_and_keeps_cause() -> None:
    error = TypeError("iterator detail")
    cause = LookupError("iterator cause")

    def values() -> Iterator[str]:
        yield "first"
        raise error from cause

    caught = conversion_failure(
        "dynamodb", "DynamoDBClient", "put_item",
        {"table_name": "CANARY", "item": {"CANARY": {"ss": values()}}},
        TypeError, "item[value#0].ss[1]", "iteration",
    )
    assert caught is error and caught.__cause__ is cause
    assert str(caught) == "iterator detail"


@service("dynamodb")
def test_existing_iterable_and_unknown_enum_acceptance_is_preserved() -> None:
    client = native_client("dynamodb", "DynamoDBClient")
    # Conversion succeeds; the terminal bridge error proves we reached it.
    for values in (("a", "b"), iter(["a", "b"])):
        with pytest.raises(RuntimeError, match="no running event loop") as caught:
            getattr(client, "put_item")({
                "table_name": "table", "item": {"tags": {"ss": values}},
                "return_values": "FUTURE_ENUM_VALUE",
            })
        assert get_conversion_context(caught.value) is None


@service("dynamodb")
@pytest.mark.asyncio
async def test_public_client_concurrency_and_no_network_dispatch() -> None:
    from rboto import DynamoDBClient, Next, OperationRequest, OperationResponse

    async def forward(request: OperationRequest, call_next: Next) -> OperationResponse:
        await asyncio.sleep(0)
        return await call_next(request)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
        clients = [
            DynamoDBClient(region="eu-west-2", endpoint_url=endpoint, interceptors=hooks)
            for hooks in ((), (forward,))
        ]

        async def fail(index: int) -> ConversionErrorContext:
            item = {"CANARY": {"l": [{"s": "valid"}] * index + [{"s": 7}]}}
            with pytest.raises(TypeError) as caught:
                await clients[index % 2].put_item(table_name="CANARY", item=item)
            context = get_conversion_context(caught.value)
            assert context is not None
            assert context.path == f"item[value#0].l[{index}].s"
            assert context.operation == "PutItem"
            return context

        contexts = await asyncio.gather(*(fail(index) for index in range(20)))
        assert len({context.path for context in contexts}) == 20
        with pytest.raises(BlockingIOError):
            listener.accept()
