import asyncio
import os

from pydantic import BaseModel, ConfigDict, Field
from rboto import s3

client = s3()
bucket = os.environ["DOCUMENT_BUCKET"]


class DocumentInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    key: str = Field(min_length=1)


class DocumentOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=True)

    content_length: int = Field(ge=0)
    content_type: str | None = None


async def inspect_document(data: DocumentInput) -> DocumentOutput:
    response = await client.head_object(bucket=bucket, key=data.key)
    return DocumentOutput.model_validate(response)


def lambda_handler(event: dict[str, object], context: object) -> dict[str, object]:
    data = DocumentInput.model_validate(event)
    result = asyncio.run(inspect_document(data))
    return result.model_dump(mode="json")
