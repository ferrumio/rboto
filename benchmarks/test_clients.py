from rboto import dynamodb, s3, sns, sqs


def test_s3_client_construction(benchmark: object) -> None:
    benchmark(lambda: s3(region="us-east-1"))


def test_sqs_client_construction(benchmark: object) -> None:
    benchmark(lambda: sqs(region="us-east-1"))


def test_sns_client_construction(benchmark: object) -> None:
    benchmark(lambda: sns(region="us-east-1"))


def test_dynamodb_client_construction(benchmark: object) -> None:
    benchmark(lambda: dynamodb(region="us-east-1"))
