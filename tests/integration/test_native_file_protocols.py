"""Representative non-S3 operations use the same file bridge."""

import os

import pytest
from rboto import ClientConfig, FileBody
from test_file_streaming import anyio_backend as anyio_backend
from test_file_streaming import peer as peer

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
pytestmark = pytest.mark.anyio


def config():
    return ClientConfig(access_key_id="testing", secret_access_key="testing", max_attempts=1)


@pytest.mark.skipif(bool(SELECTED and "lambda" not in SELECTED), reason="Lambda not selected")
async def test_lambda_file_input(peer, tmp_path):
    from rboto import LambdaClient

    source = tmp_path / "event.json"
    source.write_bytes(b'{"event": "file"}')
    async with LambdaClient(
        endpoint_url=peer.endpoint, region="eu-west-2", config=config(),
    ) as client:
        response = await client.invoke_async(function_name="test", invoke_args=FileBody(source))
    assert response.status == 202
    assert peer.received == [source.read_bytes()]


@pytest.mark.skipif(
    bool(SELECTED and "bedrock-runtime" not in SELECTED), reason="Bedrock Runtime not selected",
)
async def test_bedrock_ordinary_blob_contract_is_unchanged(peer, tmp_path):
    from rboto import BedrockRuntimeClient

    async with BedrockRuntimeClient(
        endpoint_url=peer.endpoint, region="eu-west-2", config=config(),
    ) as client:
        response = await client.invoke_model(model_id="test", body=b"{}")
        assert isinstance(response.body, bytes)
        assert response.body == peer.data
        source = tmp_path / "request.json"
        source.write_bytes(b"{}")
        with pytest.raises(TypeError, match="body"):
            await client.invoke_model(model_id="test", body=FileBody(source))


@pytest.mark.skipif(bool(SELECTED and "polly" not in SELECTED), reason="Polly not selected")
async def test_polly_response_file(peer, tmp_path):
    from rboto import PollyClient

    async with PollyClient(
        endpoint_url=peer.endpoint, region="eu-west-2", config=config(),
    ) as client:
        response = await client.synthesize_speech(
            output_format="mp3", text="test", voice_id="Joanna",
        )
        target = tmp_path / "response"
        assert await response.audio_stream.write_to(target) == len(peer.data)
    assert target.read_bytes() == peer.data
