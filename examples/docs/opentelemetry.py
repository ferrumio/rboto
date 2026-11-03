"""Use an application's OpenTelemetry providers with typed, concurrent clients."""

import asyncio

from opentelemetry import trace
from rboto import DynamoDBClient, S3Client
from rboto.integrations.opentelemetry import configure_opentelemetry


async def main() -> None:
    # Configure application providers/exporters before this call.
    configure_opentelemetry()
    s3 = S3Client(region="us-east-1")
    dynamodb = DynamoDBClient(region="us-east-1")
    with trace.get_tracer("application").start_as_current_span("inspect-resources"):
        buckets, tables = await asyncio.gather(s3.list_buckets(), dynamodb.list_tables())
        print(buckets.buckets)
        print(tables.table_names)


if __name__ == "__main__":
    asyncio.run(main())
