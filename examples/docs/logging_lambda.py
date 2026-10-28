import asyncio
from typing import Protocol

from rboto import DynamoDBClient, LoggingInterceptor, operation_context

# Configure Lambda application logs as JSON at INFO; reuse the runtime's handlers.
client = DynamoDBClient(interceptors=[LoggingInterceptor()])


class LambdaContext(Protocol):
    @property
    def aws_request_id(self) -> str: ...


async def list_tables() -> dict[str, object]:
    response = await client.list_tables()
    return {"tables": response.table_names}


def lambda_handler(event: dict[str, object], context: LambdaContext) -> dict[str, object]:
    with operation_context(correlation_id=context.aws_request_id):
        return asyncio.run(list_tables())
