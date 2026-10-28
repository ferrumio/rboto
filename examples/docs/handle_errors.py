from rboto_s3 import S3Client
from rboto_s3.exceptions import NoSuchKeyError, S3Error


async def read_optional_object(client: S3Client, bucket: str, key: str) -> bytes | None:
    try:
        response = await client.get_object(bucket=bucket, key=key)
        return await response.body.read()
    except NoSuchKeyError:
        return None
    except S3Error as error:
        print(error.operation_name, error.error_code)
        raise
