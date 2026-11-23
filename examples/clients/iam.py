#!/usr/bin/env python3
import asyncio

from rboto import IAMClient


async def main() -> None:
    async with IAMClient() as client:
        async for page in client.paginators.list_roles(path_prefix="/my-application/"):
            for role in page.roles or []:
                print(role.role_name, role.arn)


if __name__ == "__main__":
    asyncio.run(main())
