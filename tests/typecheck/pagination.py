from collections.abc import AsyncIterator
from typing import assert_type

from rboto import DynamoDBClient, LambdaClient, S3Client
from rboto_dynamodb import QueryOutput
from rboto_lambda import ListFunctionsResponse
from rboto_s3._native import ListMultipartUploadsOutput


async def pagination(ddb: DynamoDBClient, lamb: LambdaClient, s3: S3Client) -> None:
    functions = lamb.paginators.list_functions(max_items=50)
    assert_type(functions, AsyncIterator[ListFunctionsResponse])
    async for page in functions:
        assert_type(page, ListFunctionsResponse)
        for function in page.functions or []:
            assert_type(function.function_name, str | None)

    async for result in ddb.paginators.query(
        table_name="example", expression_attribute_values={":pk": {"s": "a"}},
        exclusive_start_key={"pk": {"s": "initial"}},
    ):
        assert_type(result, QueryOutput)

    async for uploads in s3.paginators.list_multipart_uploads(
        bucket="example", key_marker="a", upload_id_marker="b", max_uploads=100,
    ):
        assert_type(uploads, ListMultipartUploadsOutput)
