"""Stateful client flows against an explicitly configured LocalStack endpoint."""

import asyncio
import json
import os
import time
import uuid

import pytest

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))


def service(name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        bool(SELECTED and name not in SELECTED), reason="service not selected"
    )


@pytest.fixture
def endpoint_url() -> str:
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint is None:
        pytest.skip("AWS_ENDPOINT_URL is required for LocalStack tests")
    return endpoint


@pytest.mark.asyncio
@service("cloudwatch-logs")
async def test_logs_write_read_and_filter(endpoint_url: str) -> None:
    from rboto import cloudwatch_logs
    from rboto_cloudwatch_logs.exceptions import ResourceNotFoundError

    client = cloudwatch_logs(region="us-east-1", endpoint_url=endpoint_url)
    group = f"/rboto/test-{uuid.uuid4().hex}"
    await client.create_log_group(log_group_name=group)
    try:
        await client.create_log_stream(log_group_name=group, log_stream_name="example")
        timestamp = int(time.time() * 1000)
        written = await client.put_log_events(
            log_group_name=group,
            log_stream_name="example",
            log_events=[{"timestamp": timestamp, "message": "rboto typed event"}],
        )
        assert written.rejected_log_events_info is None
        read = await client.get_log_events(
            log_group_name=group, log_stream_name="example", start_from_head=True
        )
        assert read.events
        assert read.events[0].timestamp == timestamp
        assert read.events[0].message == "rboto typed event"
        filtered = await client.filter_log_events(
            log_group_name=group, filter_pattern='"typed"'
        )
        assert filtered.events
        assert filtered.events[0].message == "rboto typed event"
    finally:
        await client.delete_log_group(log_group_name=group)
    with pytest.raises(ResourceNotFoundError):
        await client.get_log_events(log_group_name=group, log_stream_name="example")


@pytest.mark.asyncio
@service("ssm")
async def test_secure_parameter_versions_and_missing_parameter(endpoint_url: str) -> None:
    from rboto import ssm
    from rboto_ssm.exceptions import ParameterNotFoundError

    client = ssm(region="us-east-1", endpoint_url=endpoint_url)
    name = f"/rboto/test-{uuid.uuid4().hex}"
    created = await client.put_parameter(name=name, value="first", type="SecureString")
    try:
        assert created.version == 1
        updated = await client.put_parameter(
            name=name, value="second", type="SecureString", overwrite=True
        )
        assert updated.version == 2
        current = await client.get_parameter(name=name, with_decryption=True)
        assert current.parameter is not None
        assert current.parameter.value == "second"
        assert current.parameter.version == 2
        history = await client.get_parameter_history(name=name, with_decryption=True)
        assert history.parameters is not None
        assert [(item.version, item.value) for item in history.parameters] == [
            (1, "first"),
            (2, "second"),
        ]
    finally:
        await client.delete_parameter(name=name)
    with pytest.raises(ParameterNotFoundError):
        await client.get_parameter(name=name)


@pytest.mark.asyncio
@service("kms")
async def test_kms_roundtrip_and_authenticated_context(endpoint_url: str) -> None:
    from rboto import kms
    from rboto_kms.exceptions import InvalidCiphertextError

    client = kms(region="us-east-1", endpoint_url=endpoint_url)
    created = await client.create_key(description="rboto LocalStack integration")
    assert created.key_metadata is not None
    key_id = created.key_metadata.key_id
    try:
        plaintext = b"\x00rboto binary payload\xff"
        encrypted = await client.encrypt(
            key_id=key_id, plaintext=plaintext, encryption_context={"purpose": "test"}
        )
        assert encrypted.ciphertext_blob
        assert encrypted.ciphertext_blob != plaintext
        decrypted = await client.decrypt(
            key_id=key_id,
            ciphertext_blob=encrypted.ciphertext_blob,
            encryption_context={"purpose": "test"},
        )
        assert decrypted.plaintext == plaintext
        with pytest.raises(InvalidCiphertextError):
            await client.decrypt(
                key_id=key_id,
                ciphertext_blob=encrypted.ciphertext_blob,
                encryption_context={"purpose": "wrong"},
            )
    finally:
        await client.schedule_key_deletion(key_id=key_id, pending_window_in_days=7)


@pytest.mark.asyncio
@service("sfn")
async def test_state_machine_execution_and_history(endpoint_url: str) -> None:
    from rboto import sfn

    client = sfn(region="us-east-1", endpoint_url=endpoint_url)
    created = await client.create_state_machine(
        name=f"rboto-test-{uuid.uuid4().hex}",
        definition=json.dumps(
            {"StartAt": "Done", "States": {"Done": {"Type": "Pass", "End": True}}}
        ),
        role_arn="arn:aws:iam::000000000000:role/rboto-localstack-test",
    )
    try:
        started = await client.start_execution(
            state_machine_arn=created.state_machine_arn, input='{"value":42}'
        )
        async with asyncio.timeout(30):
            while True:
                result = await client.describe_execution(execution_arn=started.execution_arn)
                if result.status != "RUNNING":
                    break
                await asyncio.sleep(0.1)
        assert result.status == "SUCCEEDED"
        assert result.output is not None
        assert json.loads(result.output) == {"value": 42}
        history = await client.get_execution_history(execution_arn=started.execution_arn)
        assert history.events
        assert history.events[-1].type == "ExecutionSucceeded"
    finally:
        await client.delete_state_machine(state_machine_arn=created.state_machine_arn)
