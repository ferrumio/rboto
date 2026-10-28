from rboto_sts import STSClient


async def identity_dict(client: STSClient) -> dict[str, object]:
    response = await client.get_caller_identity()
    return response.to_dict()
