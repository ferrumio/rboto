import asyncio
import os

from rboto import AthenaClient


async def main() -> None:
    client = AthenaClient(region=os.environ.get("AWS_REGION", "us-east-1"))
    started = await client.start_query_execution(
        query_string="SELECT n FROM UNNEST(sequence(1, 12)) AS t(n) ORDER BY n",
        work_group=os.environ["ATHENA_WORKGROUP"],
    )
    query_id = started.query_execution_id
    assert query_id is not None
    try:
        async with asyncio.timeout(60):
            while True:
                result = await client.get_query_execution(query_execution_id=query_id)
                assert result.query_execution is not None
                status = result.query_execution.status
                assert status is not None
                if status.state == "SUCCEEDED":
                    break
                if status.state in {"FAILED", "CANCELLED"}:
                    raise RuntimeError(status.state_change_reason or "Athena query failed")
                await asyncio.sleep(0.5)

        async for page in client.paginators.get_query_results(
            query_execution_id=query_id, max_results=3,
        ):
            assert page.result_set is not None
            for row in page.result_set.rows or []:
                print([cell.var_char_value for cell in row.data or []])
    finally:
        await client.stop_query_execution(query_execution_id=query_id)


if __name__ == "__main__":
    asyncio.run(main())
