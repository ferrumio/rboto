"""Protocol aliases must preserve catchable modeled exceptions through native code."""

import importlib
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
import rboto
from rboto import ClientConfig
from rboto.exceptions import ServiceError

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
CLIENT_CLASSES = {
    "sqs": "SQSClient", "sns": "SNSClient", "kms": "KMSClient", "cloudtrail": "CloudTrailClient",
}
QUEUE = "http://127.0.0.1/123456789012/audit"
ARN = "arn:aws:sqs:eu-west-2:123456789012:audit"
SQS_PARAMETERS: dict[str, dict[str, object]] = {
    "add_permission": {
        "queue_url": QUEUE, "label": "audit", "aws_account_ids": ["123456789012"],
        "actions": ["SendMessage"],
    },
    "cancel_message_move_task": {"task_handle": "audit"},
    "change_message_visibility": {
        "queue_url": QUEUE, "receipt_handle": "audit", "visibility_timeout": 0,
    },
    "change_message_visibility_batch": {
        "queue_url": QUEUE,
        "entries": [{"id": "0", "receipt_handle": "audit", "visibility_timeout": 0}],
    },
    "create_queue": {"queue_name": "audit"},
    "delete_message": {"queue_url": QUEUE, "receipt_handle": "audit"},
    "delete_message_batch": {
        "queue_url": QUEUE, "entries": [{"id": "0", "receipt_handle": "audit"}],
    },
    "delete_queue": {"queue_url": QUEUE},
    "get_queue_attributes": {"queue_url": QUEUE},
    "get_queue_url": {"queue_name": "audit"},
    "list_dead_letter_source_queues": {"queue_url": QUEUE},
    "list_message_move_tasks": {"source_arn": ARN},
    "list_queue_tags": {"queue_url": QUEUE},
    "list_queues": {},
    "purge_queue": {"queue_url": QUEUE},
    "receive_message": {"queue_url": QUEUE},
    "remove_permission": {"queue_url": QUEUE, "label": "audit"},
    "send_message": {"queue_url": QUEUE, "message_body": "audit"},
    "send_message_batch": {
        "queue_url": QUEUE, "entries": [{"id": "0", "message_body": "audit"}],
    },
    "set_queue_attributes": {"queue_url": QUEUE, "attributes": {"VisibilityTimeout": "0"}},
    "start_message_move_task": {"source_arn": ARN},
    "tag_queue": {"queue_url": QUEUE, "tags": {"audit": "audit"}},
    "untag_queue": {"queue_url": QUEUE, "tag_keys": ["audit"]},
}
BATCH = ("change_message_visibility_batch", "delete_message_batch", "send_message_batch")
MESSAGES = ("receive_message", "send_message", "send_message_batch")
MISSING_QUEUE = tuple(
    method for method in SQS_PARAMETERS if method not in {
        "cancel_message_move_task", "create_queue", "list_message_move_tasks",
        "list_queues", "start_message_move_task",
    }
)
SQS_ALIASES = (
    ("AWS.SimpleQueueService.BatchEntryIdsNotDistinct", "BatchEntryIdsNotDistinctError", BATCH),
    (
        "AWS.SimpleQueueService.BatchRequestTooLong", "BatchRequestTooLongError",
        ("send_message_batch",),
    ),
    ("AWS.SimpleQueueService.EmptyBatchRequest", "EmptyBatchRequestError", BATCH),
    ("AWS.SimpleQueueService.InvalidBatchEntryId", "InvalidBatchEntryIdError", BATCH),
    ("KMS.AccessDeniedException", "KmsAccessDeniedError", MESSAGES),
    ("KMS.DisabledException", "KmsDisabledError", MESSAGES),
    ("KMS.InvalidKeyUsageException", "KmsInvalidKeyUsageError", MESSAGES),
    ("KMS.InvalidStateException", "KmsInvalidStateError", MESSAGES),
    ("KMS.NotFoundException", "KmsNotFoundError", MESSAGES),
    ("KMS.OptInRequired", "KmsOptInRequiredError", MESSAGES),
    ("KMS.ThrottlingException", "KmsThrottledError", MESSAGES),
    (
        "AWS.SimpleQueueService.MessageNotInflight", "MessageNotInflightError",
        ("change_message_visibility",),
    ),
    ("AWS.SimpleQueueService.PurgeQueueInProgress", "PurgeQueueInProgressError", ("purge_queue",)),
    ("AWS.SimpleQueueService.QueueDeletedRecently", "QueueDeletedRecentlyError", ("create_queue",)),
    ("AWS.SimpleQueueService.NonExistentQueue", "QueueDoesNotExistError", MISSING_QUEUE),
    ("QueueAlreadyExists", "QueueNameExistsError", ("create_queue",)),
    (
        "AWS.SimpleQueueService.TooManyEntriesInBatchRequest",
        "TooManyEntriesInBatchRequestError", BATCH,
    ),
    (
        "AWS.SimpleQueueService.UnsupportedOperation", "UnsupportedOperationError",
        tuple(SQS_PARAMETERS),
    ),
)


@dataclass
class Peer:
    endpoint: str = ""
    body: bytes = b""
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)
    requests: int = 0


@pytest.fixture
def peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Peer]:
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    state = Peer()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.requests += 1
            self.send_response(400)
            self.send_header("Content-Type", state.content_type)
            self.send_header("Content-Length", str(len(state.body)))
            self.send_header("x-amzn-requestid", "alias-request")
            for name, value in state.headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(state.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


CASES = [
    ("sqs", method, wire, exception)
    for wire, exception, methods in SQS_ALIASES for method in methods
] + [
    ("sns", method, "ParameterValueInvalid", "InvalidParameterValueError")
    for method in ("publish", "publish_batch")
] + [
    ("sqs", "get_queue_attributes", "InvalidAddress", "InvalidAddressError"),
    ("sns", "publish", "InvalidParameter", "InvalidParameterError"),
    ("sqs", "get_queue_attributes", "FutureServiceFailure", "SQSError"),
    ("sns", "publish", "FutureServiceFailure", "SNSError"),
    (
        "kms", "delete_custom_key_store",
        "CustomKeyStoreHasCMKsException", "CustomKeyStoreHasCMKsError",
    ),
    ("cloudtrail", "describe_trails", "AccessDeniedException", "AccessDeniedError"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("service", "method", "wire_code", "exception_name"), [
    pytest.param(
        service, method, code, exception,
        id=f"{service}-{method}-{exception}",
        marks=pytest.mark.skipif(
            bool(SELECTED and service not in SELECTED), reason="service not selected",
        ),
    )
    for service, method, code, exception in CASES
])
async def test_modeled_error_aliases(
    peer: Peer, service: str, method: str, wire_code: str, exception_name: str,
) -> None:
    if service == "sqs":
        parameters = dict(SQS_PARAMETERS[method])
        if "queue_url" in parameters:
            parameters["queue_url"] = peer.endpoint + "/123456789012/audit"
        peer.body = json.dumps({
            "__type": (
                wire_code if exception_name == "SQSError" else exception_name.removesuffix("Error")
            ),
            "message": "modeled failure",
        }).encode()
        peer.headers["x-amzn-query-error"] = wire_code + ";Sender"
    elif service == "sns":
        peer.content_type = "text/xml"
        peer.body = (
            "<ErrorResponse><Error><Type>Sender</Type>"
            f"<Code>{escape(wire_code)}</Code><Message>modeled failure</Message>"
            "</Error><RequestId>alias-request</RequestId></ErrorResponse>"
        ).encode()
        parameters = {"topic_arn": "arn:aws:sns:eu-west-2:123456789012:audit"}
        if method == "publish":
            parameters["message"] = "audit"
        else:
            parameters["publish_batch_request_entries"] = [{"id": "0", "message": "audit"}]
    else:
        peer.body = json.dumps({"__type": wire_code, "message": "modeled failure"}).encode()
        parameters = {"custom_key_store_id": "audit"} if service == "kms" else {}
    exception = getattr(importlib.import_module(f"rboto_{service}.exceptions"), exception_name)
    assert issubclass(exception, ServiceError)
    factory = getattr(rboto, CLIENT_CLASSES[service])
    async with factory(
        region="eu-west-2", endpoint_url=peer.endpoint,
        config=ClientConfig(
            access_key_id="testing", secret_access_key="testing",
            max_attempts=1, connect_timeout=3, read_timeout=3,
        ),
    ) as client:
        with pytest.raises(exception, match="modeled failure") as caught:
            await getattr(client, method)(**parameters)
    error = caught.value
    assert type(error).__name__ == exception_name
    assert error.error_code == wire_code
    assert error.operation_name == "".join(word.title() for word in method.split("_"))
    assert error.response_metadata["HTTPStatusCode"] == 400
    assert error.response_metadata["RequestId"] == "alias-request"
    assert error.response_metadata["RetryAttempts"] == 0
    assert peer.requests == 1
