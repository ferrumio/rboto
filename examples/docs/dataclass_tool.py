from dataclasses import dataclass

from rboto_s3 import S3Client


@dataclass(frozen=True, slots=True)
class DocumentInput:
    key: str


@dataclass(frozen=True, slots=True)
class DocumentOutput:
    content_length: int
    content_type: str | None = None


async def inspect_document(
    client: S3Client,
    data: DocumentInput,
    *,
    bucket: str,
) -> DocumentOutput:
    response = await client.head_object(bucket=bucket, key=data.key)
    if response.content_length is None:
        raise ValueError("The response did not include an object size")
    return DocumentOutput(
        content_length=response.content_length,
        content_type=response.content_type,
    )
