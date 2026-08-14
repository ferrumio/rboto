from rboto import Session
from rboto.exceptions import ClientError


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
