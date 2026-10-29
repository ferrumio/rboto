from rboto import ClientConfig, S3Client, Session, STSClient, configure_logging, s3

settings = ClientConfig(
    role_arn="arn:aws:iam::123456789012:role/application",
    role_session_name="application",
    connect_timeout=2,
    read_timeout=10,
    attempt_timeout=15,
    operation_timeout=30,
    max_attempts=3,
    retry_mode="standard",
    proxy_url="http://localhost:8080",
    no_proxy="localhost",
    ca_bundle="/etc/application/company-ca.pem",
)
session = Session(region="us-east-1", profile="development", config=settings)
objects: S3Client = S3Client(session=session)
factory_objects: S3Client = s3(config=settings, region="us-east-1")
identity: STSClient = STSClient(config=settings, region="us-east-1")
configure_logging(service_levels={"sts": "OFF"})
