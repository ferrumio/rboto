from time import monotonic

from rboto import Next, OperationRequest, OperationResponse


class ListBucketsCache:
    """Use one instance per client configuration and credential scope."""

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._ttl = ttl_seconds
        self._expires = 0.0
        self._output: object | None = None

    async def __call__(self, request: OperationRequest, call_next: Next) -> OperationResponse:
        if (
            request.context.service != "s3"
            or request.context.operation != "ListBuckets"
            or request.parameters
        ):
            return await call_next(request)
        if self._output is not None and monotonic() < self._expires:
            return OperationResponse(self._output)
        response = await call_next(request)
        self._output = response.output
        self._expires = monotonic() + self._ttl
        return response
