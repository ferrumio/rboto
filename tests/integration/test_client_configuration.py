"""Credential/configuration contracts through a compiled client and an isolated HTTP peer."""

import asyncio
import io
import json
import os
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(SELECTED and "sts" not in SELECTED), reason="STS not selected"),
]

IDENTITY = b"""<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
<GetCallerIdentityResult><Arn>arn:aws:iam::123456789012:user/test</Arn>
<UserId>test</UserId><Account>123456789012</Account></GetCallerIdentityResult>
<ResponseMetadata><RequestId>identity-request</RequestId></ResponseMetadata>
</GetCallerIdentityResponse>"""
ROLE = b"""<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
<AssumeRoleResult><Credentials><AccessKeyId>assumed-key</AccessKeyId>
<SecretAccessKey>assumed-secret</SecretAccessKey><SessionToken>assumed-token</SessionToken>
<Expiration>2099-01-01T00:00:00Z</Expiration></Credentials>
<AssumedRoleUser><AssumedRoleId>test:session</AssumedRoleId>
<Arn>arn:aws:sts::123456789012:assumed-role/test/session</Arn></AssumedRoleUser>
</AssumeRoleResult><ResponseMetadata><RequestId>role-request</RequestId></ResponseMetadata>
</AssumeRoleResponse>"""


@dataclass
class Peer:
    endpoint: str = ""
    requests: list[tuple[str, str, str, bytes]] = field(default_factory=list)
    status: int = 200
    body: bytes = IDENTITY
    gate: threading.Event | None = None
    request_received: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Peer]:
    # No developer credentials, metadata endpoint, proxy, or configured profile
    # may escape into these tests.
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    state = Peer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            action = parse_qs(body.decode())["Action"][0]
            state.requests.append(
                (
                    action,
                    self.headers.get("Authorization", ""),
                    self.headers.get("X-Amz-Security-Token", ""),
                    body,
                )
            )
            state.request_received.set()
            if state.gate is not None:
                state.gate.wait(3)
            response = ROLE if action == "AssumeRole" else state.body
            self.send_response(state.status)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("x-amzn-requestid", "peer-request-id")
            self.end_headers()
            try:
                self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("AWS_ENDPOINT_URL_STS", state.endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        if state.gate is not None:
            state.gate.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


async def test_explicit_credentials_token_and_lazy_initialization(peer: Peer) -> None:
    from rboto import ClientConfig, sts

    client = sts(
        region="us-east-1",
        config=ClientConfig(
            access_key_id="explicit-key",
            secret_access_key="explicit-secret",
            session_token="token",
        ),
    )
    assert peer.requests == []
    response = await client.get_caller_identity()
    assert response.account == "123456789012"
    assert "Credential=explicit-key/" in peer.requests[0][1]
    assert peer.requests[0][2] == "token"


async def test_named_profile_supplies_assume_role_source_and_region(
    peer: Peer,
    tmp_path: Path,
) -> None:
    from rboto import ClientConfig, sts

    (tmp_path / "config").write_text("[profile source]\nregion=eu-west-2\n")
    (tmp_path / "credentials").write_text(
        "[source]\naws_access_key_id=profile-key\naws_secret_access_key=profile-secret\n"
    )
    client = sts(
        profile="source",
        config=ClientConfig(
            role_arn="arn:aws:iam::123456789012:role/test",
            role_session_name="requested-session",
            external_id="requested-external",
        ),
    )
    await client.get_caller_identity()
    await client.get_caller_identity()
    assert [call[0] for call in peer.requests] == [
        "AssumeRole",
        "GetCallerIdentity",
        "GetCallerIdentity",
    ]
    assert "Credential=profile-key/" in peer.requests[0][1]
    assert "/eu-west-2/sts/" in peer.requests[0][1]
    parameters = parse_qs(peer.requests[0][3].decode())
    assert parameters["ExternalId"] == ["requested-external"]
    assert parameters["RoleSessionName"] == ["requested-session"]
    assert "Credential=assumed-key/" in peer.requests[1][1]
    assert peer.requests[1][2] == "assumed-token"


async def test_explicit_credentials_can_assume_role(peer: Peer) -> None:
    from rboto import ClientConfig, sts

    client = sts(
        region="us-east-1",
        config=ClientConfig(
            access_key_id="source-key",
            secret_access_key="source-secret",
            role_arn="arn:aws:iam::123456789012:role/test",
        ),
    )
    await client.get_caller_identity()
    assert "Credential=source-key/" in peer.requests[0][1]
    assert "Credential=assumed-key/" in peer.requests[1][1]


async def test_missing_credentials_have_a_specific_exception(peer: Peer) -> None:
    from rboto import sts
    from rboto.exceptions import CredentialsError

    with pytest.raises(CredentialsError):
        await sts(region="us-east-1").get_caller_identity()
    assert not peer.requests


async def test_missing_region_is_configuration_error(peer: Peer) -> None:
    from rboto import sts
    from rboto.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="region"):
        await sts().get_caller_identity()
    assert not peer.requests


async def test_service_error_retains_request_id(peer: Peer) -> None:
    from rboto import ClientConfig, sts
    from rboto_sts.exceptions import STSError

    peer.status = 403
    peer.body = (
        b"<ErrorResponse><Error><Code>AccessDenied</Code>"
        b"<Message>denied</Message></Error></ErrorResponse>"
    )
    client = sts(
        region="us-east-1",
        config=ClientConfig(
            access_key_id="key",
            secret_access_key="secret",
        ),
    )
    with pytest.raises(STSError) as caught:
        await client.get_caller_identity()
    assert caught.value.request_id == "peer-request-id"
    assert caught.value.error_code == "AccessDenied"
    assert caught.value.operation_name == "GetCallerIdentity"
    assert caught.value.message == "denied"


async def test_undecodable_response_has_a_typed_bounded_error(peer: Peer) -> None:
    from rboto import ClientConfig, STSClient
    from rboto.exceptions import ResponseError

    peer.body = b"invalid XML containing a private payload"
    client = STSClient(
        region="us-east-1",
        config=ClientConfig(access_key_id="key", secret_access_key="secret"),
    )
    with pytest.raises(ResponseError) as caught:
        await client.get_caller_identity()
    assert caught.value.request_id == "peer-request-id"
    assert caught.value.operation_name == "GetCallerIdentity"
    assert "private payload" not in str(caught.value)


async def test_credential_failure_reaches_global_logging_without_secrets(
    peer: Peer, tmp_path: Path, json_logging: io.StringIO,
) -> None:
    from rboto import STSClient, configure_logging
    from rboto.exceptions import CredentialsError

    process = tmp_path / "broken-provider.py"
    process.write_text("raise SystemExit('private-provider-output')\n")
    (tmp_path / "config").write_text(
        f"[profile broken]\nregion=us-east-1\ncredential_process={sys.executable} {process}\n"
    )
    configure_logging(stream=json_logging)
    with pytest.raises(CredentialsError) as caught:
        await STSClient(profile="broken").get_caller_identity()
    record = json.loads(json_logging.getvalue())
    assert record["logger"] == "rboto.sts"
    assert record["error_type"] == "CredentialsError"
    assert "private-provider-output" not in str(caught.value) + json_logging.getvalue()
    assert not peer.requests


async def test_retry_override_counts_total_attempts(peer: Peer) -> None:
    from rboto import ClientConfig, sts
    from rboto_sts.exceptions import STSError

    peer.status = 503
    peer.body = (
        b"<ErrorResponse><Error><Code>ServiceUnavailable</Code>"
        b"<Message>later</Message></Error></ErrorResponse>"
    )
    client = sts(
        region="us-east-1",
        config=ClientConfig(
            access_key_id="key",
            secret_access_key="secret",
            max_attempts=2,
        ),
    )
    with pytest.raises(STSError):
        await client.get_caller_identity()
    assert len(peer.requests) == 2


@pytest.mark.parametrize("setting", ["operation_timeout", "attempt_timeout", "read_timeout"])
async def test_timeout_is_typed_and_does_not_block_event_loop(peer: Peer, setting: str) -> None:
    from rboto import ClientConfig, sts
    from rboto.exceptions import TimeoutError

    peer.gate = threading.Event()
    client = sts(
        region="us-east-1",
        config=ClientConfig(
            access_key_id="key",
            secret_access_key="secret",
            **{setting: 0.15},  # type: ignore[arg-type]
        ),
    )
    async with asyncio.timeout(3):
        with pytest.raises(TimeoutError):
            await client.get_caller_identity()
    assert len(peer.requests) == 1


async def test_unreachable_endpoint_has_connection_error(peer: Peer) -> None:
    from rboto import ClientConfig, sts
    from rboto.exceptions import ConnectionError

    client = sts(
        region="us-east-1",
        endpoint_url="http://127.0.0.1:1",
        config=ClientConfig(
            access_key_id="key",
            secret_access_key="secret",
            operation_timeout=2,
        ),
    )
    with pytest.raises(ConnectionError) as caught:
        await client.get_caller_identity()
    assert "secret" not in str(caught.value)
    assert caught.value.operation_name == "GetCallerIdentity"


async def test_credential_process_is_cached(peer: Peer, tmp_path: Path) -> None:
    from rboto import sts

    counter = tmp_path / "calls"
    process = tmp_path / "provider.py"
    credentials = json.dumps(
        {
            "Version": 1,
            "AccessKeyId": "process-key",
            "SecretAccessKey": "process-secret",
            "Expiration": "2099-01-01T00:00:00Z",
        }
    )
    process.write_text(
        "from pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as f: f.write('call\\n')\n"
        f"print({credentials!r})\n"
    )
    (tmp_path / "config").write_text(
        f"[profile process]\nregion=us-east-1\ncredential_process={sys.executable} {process}\n"
    )
    client = sts(profile="process")
    await asyncio.gather(*(client.get_caller_identity() for _ in range(4)))
    assert counter.read_text().splitlines() == ["call"]
    assert all("Credential=process-key/" in call[1] for call in peer.requests)


async def test_expired_process_credentials_are_refreshed(peer: Peer, tmp_path: Path) -> None:
    from rboto import sts

    counter = tmp_path / "refresh-count"
    process = tmp_path / "refresh-provider.py"
    process.write_text(
        "import json\n"
        "from datetime import datetime, timedelta, timezone\n"
        "from pathlib import Path\n"
        f"counter = Path({str(counter)!r})\n"
        "number = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(number))\n"
        "expiry = datetime.now(timezone.utc) + timedelta(seconds=1 if number == 1 else 3600)\n"
        "print(json.dumps({'Version': 1, 'AccessKeyId': f'rotated-{number}', "
        "'SecretAccessKey': 'secret', 'Expiration': expiry.isoformat()}))\n"
    )
    (tmp_path / "config").write_text(
        f"[profile refresh]\nregion=us-east-1\ncredential_process={sys.executable} {process}\n"
    )
    client = sts(profile="refresh")
    await client.get_caller_identity()
    await asyncio.sleep(1.2)
    await client.get_caller_identity()
    assert int(counter.read_text()) >= 2
    assert peer.requests[0][1].split("/")[0] != peer.requests[1][1].split("/")[0]


async def test_two_clients_keep_credentials_isolated(peer: Peer) -> None:
    from rboto import ClientConfig, sts

    clients = [
        sts(
            region="us-east-1",
            config=ClientConfig(
                access_key_id=key,
                secret_access_key="secret",
            ),
        )
        for key in ("first-client", "second-client")
    ]
    await asyncio.gather(*(client.get_caller_identity() for client in clients))
    credentials = {call[1].split("Credential=")[1].split("/")[0] for call in peer.requests}
    assert credentials == {"first-client", "second-client"}
