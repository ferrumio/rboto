#!/usr/bin/env python3
import asyncio

from rboto import CloudFormationClient


async def main() -> None:
    async with CloudFormationClient(region="eu-west-1") as client:
        async for page in client.paginators.list_stacks(
            stack_status_filter=["CREATE_COMPLETE", "UPDATE_COMPLETE"],
        ):
            for stack in page.stack_summaries or []:
                print(stack.stack_name, stack.stack_status)


if __name__ == "__main__":
    asyncio.run(main())
