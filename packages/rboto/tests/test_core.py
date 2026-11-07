from rboto import Session
from rboto.exceptions import ClientError, ResponseMetadata


def test_session_is_immutable() -> None:
    session = Session(region="us-east-1")
    assert session.region == "us-east-1"


def test_client_error_has_typed_compatibility_response() -> None:
    error = ClientError(
        message="missing",
        error_code="NoSuchKey",
        request_id="request-1",
        operation_name="GetObject",
    )

    assert error.response["Error"]["Code"] == "NoSuchKey"
    assert error.response["RequestId"] == "request-1"
    assert "GetObject" in str(error)
    assert error.response_metadata == {
        "HTTPStatusCode": None, "RequestId": "request-1", "HostId": None, "RetryAttempts": None,
    }


def test_error_context_is_copied_and_details_stay_out_of_diagnostics() -> None:
    metadata: ResponseMetadata = {
        "HTTPStatusCode": 400, "RequestId": "request-2", "HostId": None, "RetryAttempts": 1,
    }
    error = ClientError(
        "condition failed", response_metadata=metadata, details={"item": "private data"},
    )
    metadata["RequestId"] = "changed"
    assert error.request_id == "request-2"
    assert error.response_metadata["RequestId"] == error.request_id
    assert error.response["ResponseMetadata"] is error.response_metadata
    assert error.details == {"item": "private data"}
    assert "private data" not in str(error) + repr(error) + repr(error.response)
