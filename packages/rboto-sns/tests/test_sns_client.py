from rboto import sns
from rboto.exceptions import ServiceError
from rboto_sns import SNSClient
from rboto_sns.exceptions import NotFoundError, SNSError


def test_client_construction_performs_no_io() -> None:
    client = sns(region="us-east-1")
    assert isinstance(client, SNSClient)


def test_service_exception_inheritance() -> None:
    error = NotFoundError(
        message="missing",
        error_code="NotFound",
        operation_name="GetTopicAttributes",
    )
    assert isinstance(error, SNSError)
    assert isinstance(error, ServiceError)
