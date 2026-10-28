from pydantic import BaseModel, ConfigDict, Field
from rboto import S3Client


class DocumentInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    key: str = Field(min_length=1, description="Object key within the document bucket")


class DocumentOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=True)

    content_length: int = Field(ge=0)
    content_type: str | None = None


async def inspect_document(
    client: S3Client,
    data: DocumentInput,
    *,
    bucket: str,
) -> DocumentOutput:
    response = await client.head_object(bucket=bucket, key=data.key)
    return DocumentOutput.model_validate(response)


async def run_tool(client: S3Client, arguments_json: str, *, bucket: str) -> str:
    data = DocumentInput.model_validate_json(arguments_json)
    result = await inspect_document(client, data, bucket=bucket)
    return result.model_dump_json()


input_schema = DocumentInput.model_json_schema()
output_schema = DocumentOutput.model_json_schema(mode="serialization")
