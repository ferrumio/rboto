import inspect
from collections.abc import Awaitable, Callable

import pytest
from rboto_dynamodb import DynamoDBClient
from rboto_dynamodb._native import DynamoDBClient as NativeDynamoDBClient
from rboto_s3 import S3Client
from rboto_s3._native import S3Client as NativeS3Client
from rboto_sns import SNSClient
from rboto_sns._native import SNSClient as NativeSNSClient
from rboto_sqs import SQSClient
from rboto_sqs._native import SQSClient as NativeSQSClient

CLIENTS = (
    (S3Client, NativeS3Client, 106),
    (SNSClient, NativeSNSClient, 42),
    (SQSClient, NativeSQSClient, 23),
    (DynamoDBClient, NativeDynamoDBClient, 57),
)


class NativeSpy:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __getattr__(
        self, name: str
    ) -> Callable[[dict[str, object]], Awaitable[object]]:
        async def call(params: dict[str, object]) -> object:
            self.calls.append((name, params))
            return self.result

        return call


def generated_methods(client_class: type[object]) -> dict[str, Callable[..., object]]:
    return {
        name: method
        for name, method in client_class.__dict__.items()
        if inspect.iscoroutinefunction(method)
    }


@pytest.mark.parametrize(("client_class", "native_class", "expected_count"), CLIENTS)
def test_every_python_method_has_a_native_rust_method(
    client_class: type[object],
    native_class: type[object],
    expected_count: int,
) -> None:
    python_methods = set(generated_methods(client_class))
    native_methods = {name for name in dir(native_class) if not name.startswith("_")}

    assert len(python_methods) == expected_count
    assert python_methods == native_methods


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_class", "_native_class", "_expected_count"), CLIENTS)
async def test_every_python_method_dispatches_to_its_native_counterpart(
    client_class: type[object],
    _native_class: type[object],
    _expected_count: int,
) -> None:
    client = client_class(region="us-east-1")
    sentinel = object()
    spy = NativeSpy(sentinel)
    client._native = spy  # type: ignore[attr-defined]

    for name, method in generated_methods(client_class).items():
        signature = inspect.signature(method)
        required = {
            parameter_name: object()
            for parameter_name, parameter in signature.parameters.items()
            if parameter_name != "self"
            and parameter.default is inspect.Parameter.empty
        }

        result = await getattr(client, name)(**required)
        called_name, params = spy.calls.pop()

        assert called_name == name
        assert all(value in params.values() for value in required.values())
        if signature.return_annotation == "None":
            assert result is None
        else:
            assert result is sentinel
