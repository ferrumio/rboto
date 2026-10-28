from rboto import Next, OperationRequest, OperationResponse


async def limit_page_size(request: OperationRequest, call_next: Next) -> OperationResponse:
    if request.context.service == "s3" and request.context.operation == "ListObjectsV2":
        limit = request.parameters.get("max_keys", 100)
        if not isinstance(limit, int):
            raise TypeError("max_keys must be an integer")
        request.parameters["max_keys"] = min(limit, 100)
    return await call_next(request)
