from unittest.mock import AsyncMock, create_autospec

import pytest
from rboto_sts import STSClient


async def check_identity(client: STSClient) -> None:
    await client.get_caller_identity()


@pytest.mark.asyncio
async def test_identity_request() -> None:
    client = create_autospec(STSClient, instance=True)
    await check_identity(client)
    operation: AsyncMock = client.get_caller_identity
    operation.assert_awaited_once_with()
