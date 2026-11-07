from typing import assert_type

from rboto.exceptions import ClientError, ResponseMetadata
from rboto_bedrock_runtime.exceptions import ModelStreamError
from rboto_dynamodb.exceptions import ConditionalCheckFailedError
from rboto_dynamodb.types import AttributeValueOutput


def handle(error: ClientError) -> None:
    assert_type(error.response_metadata, ResponseMetadata)
    assert_type(error.response_metadata["HTTPStatusCode"], int | None)
    assert_type(error.response_metadata["RetryAttempts"], int | None)
    assert_type(error.response["ResponseMetadata"]["RequestId"], str | None)
    if isinstance(error, ModelStreamError):
        assert_type(error.details.get("original_status_code"), int | None)
        assert_type(error.details.get("original_message"), str | None)
    if isinstance(error, ConditionalCheckFailedError):
        item = error.details.get("item")
        if item is not None:
            assert_type(item, dict[str, AttributeValueOutput])
