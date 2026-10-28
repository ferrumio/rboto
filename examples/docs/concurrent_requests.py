import asyncio

from rboto import SSMClient


async def read_settings(client: SSMClient) -> None:
    database, queue = await asyncio.gather(
        client.get_parameter(name="/app/database"),
        client.get_parameter(name="/app/queue"),
    )
    for response in (database, queue):
        if response.parameter is not None:
            print(response.parameter.name, response.parameter.value)
