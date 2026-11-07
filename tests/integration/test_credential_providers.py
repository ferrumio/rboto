"""Credential chains through compiled clients, fabricated caches and local HTTP peers."""

import asyncio
import hashlib
import json
import os
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
pytestmark = pytest.mark.asyncio
sts_only = pytest.mark.skipif(bool(SELECTED and "sts" not in SELECTED), reason="STS not selected")
bedrock_only = pytest.mark.skipif(
    bool(SELECTED and "bedrock-runtime" not in SELECTED), reason="Bedrock Runtime not selected",
)
IDENTITY = b"""<GetCallerIdentityResponse><GetCallerIdentityResult>
<Account>123456789012</Account></GetCallerIdentityResult></GetCallerIdentityResponse>"""
EXPIRATION = "2099-01-01T00:00:00Z"


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class ProviderPeer:
    endpoint: str = ""
    requests: list[Request] = field(default_factory=list)
    responses: dict[tuple[str, str], tuple[int, bytes]] = field(default_factory=dict)

    def respond(self, method: str, path: str, body: object, status: int = 200) -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.responses[method, path] = status, data

    def matching(self, method: str, path: str) -> list[Request]:
        return [
            request for request in self.requests
            if request.method == method and urlsplit(request.path).path == path
        ]


@pytest.fixture
def provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ProviderPeer]:
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in (
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        ):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_LOGIN_CACHE_DIRECTORY", str(tmp_path / "login"))
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    # SSO has no cache-directory override. Only this test's disposable home is used.
    monkeypatch.setenv("HOME", str(tmp_path))
    state = ProviderPeer()
    state.respond("POST", "/", IDENTITY)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def respond(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.requests.append(Request(
                self.command, self.path,
                {key.lower(): value for key, value in self.headers.items()}, body,
            ))
            status, response = state.responses.get(
                (self.command, urlsplit(self.path).path), (404, b"unexpected local request"),
            )
            if self.command == "POST" and body.startswith(b"Action=AssumeRole"):
                action = parse_qs(body.decode())["Action"][0]
                response = (
                    f"<{action}Response><{action}Result><Credentials>"
                    "<AccessKeyId>role-key</AccessKeyId><SecretAccessKey>role-secret</SecretAccessKey>"
                    f"<SessionToken>role-token</SessionToken><Expiration>{EXPIRATION}</Expiration>"
                    f"</Credentials></{action}Result></{action}Response>"
                ).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(response)))
            content_type = "text/xml" if response.startswith(b"<") else "application/json"
            self.send_header("Content-Type", content_type)
            if self.command == "PUT":
                self.send_header("x-aws-ec2-metadata-token-ttl-seconds", "21600")
            self.end_headers()
            self.wfile.write(response)

        do_GET = respond
        do_POST = respond
        do_PUT = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    for service in ("STS", "SSO", "SSO_OIDC", "SIGNIN", "BEDROCK_RUNTIME"):
        monkeypatch.setenv(f"AWS_ENDPOINT_URL_{service}", state.endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@sts_only
@pytest.mark.parametrize("source", ["profile", "environment", "explicit"])
async def test_credential_and_region_precedence(
    provider: ProviderPeer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    from rboto import ClientConfig, STSClient

    (tmp_path / "config").write_text("[profile selected]\nregion=eu-west-1\n")
    (tmp_path / "credentials").write_text(
        "[selected]\naws_access_key_id=profile-key\naws_secret_access_key=profile-secret\n"
    )
    monkeypatch.setenv("AWS_PROFILE", "selected")
    monkeypatch.delenv("AWS_REGION")
    config = None
    region = None
    expected_region = "eu-west-1"
    if source != "profile":
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "environment-key")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "environment-secret")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "environment-token")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
        expected_region = "eu-west-2"
    if source == "explicit":
        config = ClientConfig(access_key_id="explicit-key", secret_access_key="explicit-secret")
        region = "eu-central-1"
        expected_region = region
    async with STSClient(region=region, config=config) as client:
        await client.get_caller_identity()
    request = provider.requests[0]
    assert f"Credential={source}-key/" in request.headers["authorization"]
    assert f"/{expected_region}/sts/" in request.headers["authorization"]
    assert request.headers.get("x-amz-security-token") == (
        "environment-token" if source == "environment" else None
    )


@sts_only
async def test_region_and_endpoint_environment_precedence(
    provider: ProviderPeer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rboto import ClientConfig, STSClient

    config = ClientConfig(access_key_id="key", secret_access_key="secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:1")
    async with STSClient(config=config) as client:
        await client.get_caller_identity()
    assert "/eu-west-2/sts/" in provider.requests[0].headers["authorization"]
    # A service endpoint beats the global one; an explicit endpoint beats both.
    monkeypatch.setenv("AWS_ENDPOINT_URL_STS", "http://127.0.0.1:1")
    async with STSClient(config=config, endpoint_url=provider.endpoint) as client:
        await client.get_caller_identity()
    assert len(provider.requests) == 2


@sts_only
@pytest.mark.parametrize("source", ["environment", "profile"])
async def test_web_identity_uses_token_file_and_cached_role_credentials(
    provider: ProviderPeer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    from rboto import STSClient

    token = tmp_path / "web-token"
    token.write_text("fabricated-web-identity-token")
    role = "arn:aws:iam::123456789012:role/web"
    if source == "environment":
        monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(token))
        monkeypatch.setenv("AWS_ROLE_ARN", role)
        monkeypatch.setenv("AWS_ROLE_SESSION_NAME", "web-session")
    else:
        (tmp_path / "config").write_text(
            f"[default]\nrole_arn={role}\nweb_identity_token_file={token}\n"
            "role_session_name=web-session\n"
        )
    async with STSClient() as client:
        await client.get_caller_identity()
        await client.get_caller_identity()
    assert len(provider.requests) == 3
    request = provider.requests[0]
    assert "authorization" not in request.headers
    assert parse_qs(request.body.decode()) == {
        "Action": ["AssumeRoleWithWebIdentity"], "Version": ["2011-06-15"],
        "RoleArn": [role], "RoleSessionName": ["web-session"],
        "WebIdentityToken": ["fabricated-web-identity-token"],
    }
    assert all(
        "Credential=role-key/" in request.headers["authorization"]
        and request.headers["x-amz-security-token"] == "role-token"
        for request in provider.requests[1:]
    )


@sts_only
@pytest.mark.parametrize("token_file", [False, True])
async def test_container_credentials_and_authorization_token_precedence(
    provider: ProviderPeer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token_file: bool,
) -> None:
    from rboto import STSClient

    provider.respond("GET", "/credentials", {
        "AccessKeyId": "container-key", "SecretAccessKey": "container-secret",
        "Token": "container-session", "Expiration": EXPIRATION,
    })
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", provider.endpoint + "/credentials")
    monkeypatch.setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN", "environment-authorization")
    if token_file:
        token = tmp_path / "container-token"
        token.write_text("file-authorization")
        monkeypatch.setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", str(token))
    async with STSClient() as client:
        await asyncio.gather(*(client.get_caller_identity() for _ in range(4)))
    requests = provider.matching("GET", "/credentials")
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == (
        "file-authorization" if token_file else "environment-authorization"
    )
    assert all(
        "Credential=container-key/" in request.headers["authorization"]
        and request.headers["x-amz-security-token"] == "container-session"
        for request in provider.matching("POST", "/")
    )


@sts_only
async def test_imdsv2_uses_session_token_and_caches_credentials(
    provider: ProviderPeer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rboto import STSClient

    profile_path = "/latest/meta-data/iam/security-credentials/"
    provider.respond("PUT", "/latest/api/token", b"metadata-token")
    provider.respond("GET", profile_path, b"instance-role")
    provider.respond("GET", profile_path + "instance-role", {
        "Code": "Success", "AccessKeyId": "instance-key", "SecretAccessKey": "instance-secret",
        "Token": "instance-session", "Expiration": EXPIRATION,
    })
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "false")
    monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", provider.endpoint)
    async with STSClient() as client:
        await client.get_caller_identity()
        await client.get_caller_identity()
    assert len(provider.matching("PUT", "/latest/api/token")) == 1
    assert len(provider.matching("GET", profile_path + "instance-role")) == 1
    assert all(
        request.headers["x-aws-ec2-metadata-token"] == "metadata-token"
        for request in provider.requests if request.method == "GET"
    )
    assert all(
        "Credential=instance-key/" in request.headers["authorization"]
        for request in provider.matching("POST", "/")
    )


@sts_only
@pytest.mark.parametrize("modern", [False, True])
async def test_sso_cached_session_supplies_role_credentials(
    provider: ProviderPeer, tmp_path: Path, modern: bool,
) -> None:
    from rboto import STSClient

    start_url = "https://example.awsapps.com/start"
    if modern:
        profile = (
            "[default]\nsso_session=test-session\nsso_account_id=123456789012\n"
            "sso_role_name=Developer\n[sso-session test-session]\n"
            f"sso_start_url={start_url}\nsso_region=eu-west-2\n"
        )
    else:
        profile = (
            f"[default]\nsso_start_url={start_url}\nsso_region=eu-west-2\n"
            "sso_account_id=123456789012\nsso_role_name=Developer\n"
        )
    (tmp_path / "config").write_text(profile)
    cache = tmp_path / ".aws/sso/cache"
    cache.mkdir(parents=True)
    identifier = "test-session" if modern else start_url
    filename = hashlib.sha1(identifier.encode(), usedforsecurity=False).hexdigest() + ".json"
    (cache / filename).write_text(json.dumps({
        "accessToken": "cached-sso-token", "expiresAt": EXPIRATION,
        "startUrl": start_url, "region": "eu-west-2",
    }))
    provider.respond("GET", "/federation/credentials", {
        "roleCredentials": {
            "accessKeyId": "sso-key", "secretAccessKey": "sso-secret",
            "sessionToken": "sso-session", "expiration": 4070908800000,
        },
    })
    async with STSClient() as client:
        await client.get_caller_identity()
        await client.get_caller_identity()
    requests = provider.matching("GET", "/federation/credentials")
    assert len(requests) == 1
    assert requests[0].headers["x-amz-sso_bearer_token"] == "cached-sso-token"
    assert parse_qs(urlsplit(requests[0].path).query) == {
        "account_id": ["123456789012"], "role_name": ["Developer"],
    }
    assert all(
        "Credential=sso-key/" in request.headers["authorization"]
        for request in provider.matching("POST", "/")
    )


def login_cache(tmp_path: Path, *, expired: bool = False) -> Path:
    session = "arn:aws:iam::123456789012:user/test-login"
    (tmp_path / "config").write_text(f"[default]\nlogin_session={session}\n")
    cache = tmp_path / "login"
    cache.mkdir()
    key = subprocess.run(
        ["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout
    path = cache / (hashlib.sha256(session.encode()).hexdigest() + ".json")
    path.write_text(json.dumps({
        "accessToken": {
            "accessKeyId": "login-key", "secretAccessKey": "login-secret",
            "sessionToken": "login-session", "accountId": "123456789012",
            "expiresAt": "2000-01-01T00:00:00Z" if expired else EXPIRATION,
        },
        "tokenType": "aws_sigv4", "refreshToken": "login-refresh",
        "clientId": "arn:aws:signin:::devtools/same-device", "dpopKey": key,
    }))
    return path


@sts_only
async def test_login_cached_credentials_are_used_without_refresh(
    provider: ProviderPeer, tmp_path: Path,
) -> None:
    from rboto import STSClient

    login_cache(tmp_path)
    async with STSClient() as client:
        await client.get_caller_identity()
        await client.get_caller_identity()
    assert len(provider.requests) == 2
    assert all(
        "Credential=login-key/" in request.headers["authorization"]
        and request.headers["x-amz-security-token"] == "login-session"
        for request in provider.requests
    )


@sts_only
async def test_login_refresh_updates_credentials_and_disk_cache(
    provider: ProviderPeer, tmp_path: Path,
) -> None:
    from rboto import STSClient

    cache = login_cache(tmp_path, expired=True)
    provider.respond("POST", "/v1/token", {
        "accessToken": {
            "accessKeyId": "refreshed-key", "secretAccessKey": "refreshed-secret",
            "sessionToken": "refreshed-session",
        },
        "tokenType": "aws_sigv4", "expiresIn": 3600, "refreshToken": "rotated-refresh",
    })
    async with STSClient() as client:
        await client.get_caller_identity()
        await client.get_caller_identity()
    requests = provider.matching("POST", "/v1/token")
    assert len(requests) == 1
    assert "dpop" in requests[0].headers
    assert json.loads(requests[0].body)["refreshToken"] == "login-refresh"
    assert all(
        "Credential=refreshed-key/" in request.headers["authorization"]
        for request in provider.matching("POST", "/")
    )
    assert json.loads(cache.read_text())["accessToken"]["accessKeyId"] == "refreshed-key"
    assert json.loads(cache.read_text())["refreshToken"] == "rotated-refresh"


@sts_only
@pytest.mark.parametrize("failure", ["missing", "malformed", "refresh"])
async def test_login_failure_is_typed_and_does_not_leak_cache_data(
    provider: ProviderPeer, tmp_path: Path, failure: str,
) -> None:
    from rboto import STSClient
    from rboto.exceptions import CredentialsError

    cache = login_cache(tmp_path, expired=failure == "refresh")
    if failure == "missing":
        cache.unlink()
    elif failure == "malformed":
        cache.write_text("private-cache-data")
    else:
        provider.respond("POST", "/v1/token", {
            "error": "TOKEN_EXPIRED", "error_description": "private-provider-data",
        }, status=403)
    async with STSClient() as client:
        with pytest.raises(CredentialsError) as caught:
            await client.get_caller_identity()
    assert "private-" not in str(caught.value)
    assert "login-refresh" not in str(caught.value)
    assert not provider.matching("POST", "/")


@bedrock_only
async def test_bedrock_environment_bearer_token_without_signing_credentials(
    provider: ProviderPeer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rboto import BedrockRuntimeClient

    path = "/model/example/invoke"
    provider.respond("POST", path, {"result": "ok"})
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-api-token")
    async with BedrockRuntimeClient(endpoint_url=provider.endpoint) as client:
        output = await client.invoke_model(
            model_id="example", body=b'{"prompt":"hello"}', content_type="application/json",
        )
        assert json.loads(output.body) == {"result": "ok"}
    assert len(provider.requests) == 1
    assert provider.requests[0].headers["authorization"] == "Bearer bedrock-api-token"
    assert "x-amz-security-token" not in provider.requests[0].headers
