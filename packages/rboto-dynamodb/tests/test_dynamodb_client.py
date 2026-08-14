from rboto import dynamodb
from rboto_dynamodb import DynamoDBClient
from rboto_dynamodb.exceptions import DynamoDBError


def test_dynamodb_factory_and_error_hierarchy() -> None:
    client = dynamodb(region="us-east-1")
    assert isinstance(client, DynamoDBClient)
    assert hasattr(client, "get_item")
    assert issubclass(DynamoDBError, Exception)
