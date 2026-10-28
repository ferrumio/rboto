from rboto import DynamoDBClient, S3Client, SNSClient, SQSClient


def test_s3_client_construction(benchmark: object) -> None:
    benchmark(lambda: S3Client(region="us-east-1"))


def test_sqs_client_construction(benchmark: object) -> None:
    benchmark(lambda: SQSClient(region="us-east-1"))


def test_sns_client_construction(benchmark: object) -> None:
    benchmark(lambda: SNSClient(region="us-east-1"))


def test_dynamodb_client_construction(benchmark: object) -> None:
    benchmark(lambda: DynamoDBClient(region="us-east-1"))
