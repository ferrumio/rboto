from rboto import Session, s3
from rboto.exceptions import ServiceError
from rboto_s3 import S3Client
from rboto_s3.exceptions import NoSuchKeyError, S3Error


def test_client_construction_performs_no_io() -> None:
    client = s3(region="us-east-1")
    assert isinstance(client, S3Client)


def test_session_configuration_creates_client() -> None:
    client = s3(session=Session(region="us-west-2"))
    assert isinstance(client, S3Client)


def test_service_exception_inheritance() -> None:
    error = NoSuchKeyError(
        message="missing",
        error_code="NoSuchKey",
        operation_name="GetObject",
    )
    assert isinstance(error, S3Error)
    assert isinstance(error, ServiceError)
