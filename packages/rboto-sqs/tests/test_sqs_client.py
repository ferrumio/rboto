from rboto import sqs
from rboto_sqs import SQSClient
from rboto_sqs.exceptions import SQSError


def test_sqs_factory_and_error_hierarchy() -> None:
    client = sqs(region="us-east-1")
    assert isinstance(client, SQSClient)
    assert hasattr(client, "send_message")
    assert issubclass(SQSError, Exception)
