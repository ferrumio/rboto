import json
from collections.abc import AsyncIterator

from rboto import cloudwatch_logs, kms, sesv2, sfn, ssm
from rboto_cloudwatch_logs.types import InputLogEvent, StartLiveTailResponseStream
from rboto_sesv2.types import EmailContent


async def logs(group: str, stream: str) -> None:
    event: InputLogEvent = {"timestamp": 1792972800000, "message": "example"}
    result = await cloudwatch_logs().put_log_events(
        log_group_name=group, log_stream_name=stream, log_events=[event]
    )
    if result.rejected_log_events_info is not None:
        index: int | None = result.rejected_log_events_info.too_old_log_event_end_index
        assert index is None or index >= 0


async def live_tail(group_arn: str) -> AsyncIterator[StartLiveTailResponseStream]:
    result = await cloudwatch_logs().start_live_tail(log_group_identifiers=[group_arn])
    async for event in result["response_stream"]:
        yield event


async def configuration(name: str) -> str | None:
    response = await ssm().get_parameter(name=name, with_decryption=True)
    return response.parameter.value if response.parameter is not None else None


async def encryption(key_id: str, value: bytes) -> bytes | None:
    client = kms()
    encrypted = await client.encrypt(
        key_id=key_id, plaintext=value, encryption_context={"purpose": "example"}
    )
    if encrypted.ciphertext_blob is None:
        return None
    decrypted = await client.decrypt(
        key_id=key_id,
        ciphertext_blob=encrypted.ciphertext_blob,
        encryption_context={"purpose": "example"},
    )
    return decrypted.plaintext


async def workflow(state_machine_arn: str) -> str:
    result = await sfn().start_execution(
        state_machine_arn=state_machine_arn, input=json.dumps({"value": 42})
    )
    return result.execution_arn


async def email(sender: str, recipient: str) -> str | None:
    content: EmailContent = {
        "simple": {
            "subject": {"data": "Example", "charset": "UTF-8"},
            "body": {"text": {"data": "Body", "charset": "UTF-8"}},
            "attachments": [
                {"raw_content": b"example", "file_name": "example.txt"}
            ],
        }
    }
    response = await sesv2().send_email(
        from_email_address=sender,
        destination={"to_addresses": [recipient]},
        content=content,
    )
    return response.message_id
