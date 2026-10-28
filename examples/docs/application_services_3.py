from rboto import SSMClient


async def read_parameter(name: str) -> str | None:
    response = await SSMClient(region="us-east-1").get_parameter(
        name=name,
        with_decryption=True,
    )
    return response.parameter.value if response.parameter is not None else None
