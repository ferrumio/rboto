from rboto_ssm import SSMClient


async def read_parameter(client: SSMClient, name: str) -> str | None:
    response = await client.get_parameter(name=name, with_decryption=True)
    if response.parameter is None:
        return None
    return response.parameter.value
