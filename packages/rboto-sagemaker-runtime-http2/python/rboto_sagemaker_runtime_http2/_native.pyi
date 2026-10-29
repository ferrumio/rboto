from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from typing import final
from rboto.config import ClientConfig
from . import types as _types
from .types import JSONValue

from .types import InvokeEndpointWithBidirectionalStreamOutput


@final
class ByteStream:
    def __aiter__(self) -> AsyncIterator[bytes]: ...
    def __anext__(self) -> Awaitable[bytes]: ...
    def read(self) -> Awaitable[bytes]: ...

@final
class ResponseStreamEventReceiver:
    def __aiter__(self) -> AsyncIterator[_types.ResponseStreamEvent]: ...
    def __anext__(self) -> Awaitable[_types.ResponseStreamEvent]: ...




@final
class SageMakerRuntimeHTTP2Client:
    def __init__(
        self,
        *,
        region: str | None = None,
        profile: str | None = None,
        endpoint_url: str | None = None,
        config: ClientConfig | None = None,
    ) -> None: ...

    def invoke_endpoint_with_bidirectional_stream(self, params: dict[str, object], observation: dict[str, object] | None = None) -> Awaitable[InvokeEndpointWithBidirectionalStreamOutput]: ...
