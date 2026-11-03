from rboto import S3Client, SQSClient, configure_runtime

configure_runtime(worker_threads=2)

s3 = S3Client(region="us-east-1")
sqs = SQSClient(region="us-east-1")

# Each service starts its own two-worker runtime on its first native operation.
# Creating these clients does not start either pool.
