"""Compiled transport contracts against private HTTP/TLS peers and forwarding proxies."""

import asyncio
import base64
import http.client
import io
import json
import os
import select
import socket
import ssl
import subprocess
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest
from rboto import ClientConfig, configure_logging
from rboto.exceptions import ConfigurationError, ConnectionError, TimeoutError

if TYPE_CHECKING:
    from rboto import STSClient

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(SELECTED and "sts" not in SELECTED), reason="STS not selected"),
]

IDENTITY = b"""<GetCallerIdentityResponse>
<GetCallerIdentityResult><Account>123456789012</Account></GetCallerIdentityResult>
<ResponseMetadata><RequestId>transport-request</RequestId></ResponseMetadata>
</GetCallerIdentityResponse>"""
ROLE = b"""<AssumeRoleResponse><AssumeRoleResult><Credentials>
<AccessKeyId>assumed-key</AccessKeyId><SecretAccessKey>assumed-secret</SecretAccessKey>
<SessionToken>assumed-token</SessionToken><Expiration>2099-01-01T00:00:00Z</Expiration>
</Credentials></AssumeRoleResult></AssumeRoleResponse>"""


@dataclass
class Peer:
    endpoint: str = ""
    requests: list[tuple[str, dict[str, str], bytes]] = field(default_factory=list)
    gate: threading.Event | None = None


@dataclass
class Lab:
    ca: Path
    certificate: Path
    key: Path
    stack: ExitStack

    def start(self, *, secure: bool = False, proxy: bool = False) -> Peer:
        peer = Peer()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_CONNECT(self) -> None:
                assert proxy
                peer.requests.append((
                    self.path, {key.lower(): value for key, value in self.headers.items()}, b"",
                ))
                host, port = self.path.rsplit(":", 1)
                # Never allow the test proxy to reach anything except local peers.
                assert host in ("localhost", "127.0.0.1")
                with socket.create_connection((host, int(port)), timeout=3) as upstream:
                    self.send_response(200)
                    self.end_headers()
                    sockets = (self.connection, upstream)
                    while True:
                        readable, _, _ = select.select(sockets, (), (), 3)
                        if not readable:
                            return
                        for source in readable:
                            try:
                                data = source.recv(65536)
                                if not data:
                                    return
                                destination = (
                                    upstream if source is self.connection else self.connection
                                )
                                destination.sendall(data)
                            except OSError:
                                return

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                peer.requests.append((
                    self.path, {key.lower(): value for key, value in self.headers.items()}, body,
                ))
                if peer.gate is not None:
                    peer.gate.wait(3)
                if proxy:
                    target = urlsplit(self.path)
                    assert target.scheme == "http"
                    assert target.hostname in ("localhost", "127.0.0.1")
                    upstream = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
                    try:
                        headers = {
                            key: value for key, value in self.headers.items()
                            if key.lower() not in ("proxy-authorization", "proxy-connection")
                        }
                        upstream.request("POST", target.path or "/", body, headers)
                        result = upstream.getresponse()
                        response = result.read()
                        status = result.status
                    finally:
                        upstream.close()
                else:
                    action = parse_qs(body.decode())["Action"][0]
                    response = ROLE if action == "AssumeRole" else IDENTITY
                    status = 200
                self.send_response(status)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                try:
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        if secure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.certificate, self.key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        peer.endpoint = f"{'https' if secure else 'http'}://localhost:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close() -> None:
            if peer.gate is not None:
                peer.gate.set()
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.stack.callback(close)
        return peer


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    directory = tmp_path_factory.mktemp("transport-ca")
    ca, certificate, key = (directory / name for name in ("ca.pem", "server.pem", "server.key"))
    extensions = directory / "extensions"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost\n"
    )
    commands = [
        ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-subj", "/CN=rboto-test-ca", "-keyout", "ca.key", "-out", str(ca)],
        ["req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
         "-keyout", str(key), "-out", "server.csr"],
        ["x509", "-req", "-in", "server.csr", "-CA", str(ca), "-CAkey", "ca.key",
         "-CAcreateserial", "-days", "2", "-extfile", str(extensions), "-out", str(certificate)],
    ]
    for command in commands:
        subprocess.run(
            ["openssl", *command], cwd=directory, check=True, capture_output=True, timeout=20,
        )
    return ca, certificate, key


@pytest.fixture
def lab(
    certificates: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> Iterator[Lab]:
    for name in tuple(os.environ):
        if name.startswith("AWS_") or name.lower() in (
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        ):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    with ExitStack() as stack:
        yield Lab(*certificates, stack)


def client(endpoint: str, **settings: object) -> "STSClient":
    from rboto import STSClient

    return STSClient(
        region="us-east-1",
        endpoint_url=endpoint,
        config=ClientConfig(
            access_key_id="source-key", secret_access_key="source-secret",
            max_attempts=1, operation_timeout=3,
            **settings,  # type: ignore[arg-type]
        ),
    )


async def test_authenticated_http_proxy_and_concurrent_bypass(
    lab: Lab, monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin, proxy = lab.start(), lab.start(proxy=True)
    # Environment bypass must not silently override explicit per-client rules.
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    before = dict(os.environ)
    proxy_url = proxy.endpoint.replace("://", "://proxy-user:proxy-password@")
    proxied = client(origin.endpoint, proxy_url=proxy_url)
    bypassed = client(origin.endpoint, proxy_url=proxy_url, no_proxy="localhost")
    results = await asyncio.gather(
        proxied.get_caller_identity(), bypassed.get_caller_identity(),
    )
    assert [result.account for result in results] == ["123456789012"] * 2
    assert len(proxy.requests) == 1
    path, headers, _ = proxy.requests[0]
    assert path == origin.endpoint + "/"
    expected = base64.b64encode(b"proxy-user:proxy-password").decode()
    assert headers["proxy-authorization"] == f"Basic {expected}"
    assert len(origin.requests) == 2
    assert all("proxy-authorization" not in headers for _, headers, _ in origin.requests)
    assert dict(os.environ) == before


async def test_custom_trust_isolated_and_hostname_checks_preserved(lab: Lab) -> None:
    origin = lab.start(secure=True)
    trusted = client(origin.endpoint, ca_bundle=str(lab.ca))
    assert (await trusted.get_caller_identity()).account == "123456789012"
    for untrusted in (
        client(origin.endpoint),
        client(origin.endpoint.replace("localhost", "127.0.0.1"), ca_bundle=str(lab.ca)),
    ):
        with pytest.raises(ConnectionError):
            await untrusted.get_caller_identity()
    assert len(origin.requests) == 1


@pytest.mark.parametrize("secure_proxy", [False, True])
async def test_connect_proxy_and_assume_role_share_transport(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, secure_proxy: bool,
) -> None:
    origin, proxy = lab.start(secure=True), lab.start(proxy=True, secure=secure_proxy)
    monkeypatch.setenv("AWS_ENDPOINT_URL_STS", origin.endpoint)
    configured = client(
        origin.endpoint,
        proxy_url=proxy.endpoint.replace("://", "://proxy-user:proxy-password@"),
        ca_bundle=str(lab.ca),
        role_arn="arn:aws:iam::123456789012:role/example",
    )
    assert (await configured.get_caller_identity()).account == "123456789012"
    assert len(proxy.requests) >= 1
    assert all(path == urlsplit(origin.endpoint).netloc for path, _, _ in proxy.requests)
    assert all("proxy-authorization" in headers for _, headers, _ in proxy.requests)
    assert all("proxy-authorization" not in headers for _, headers, _ in origin.requests)
    assert [parse_qs(body.decode())["Action"][0] for _, _, body in origin.requests] == [
        "AssumeRole", "GetCallerIdentity",
    ]
    assert "Credential=source-key/" in origin.requests[0][1]["authorization"]
    assert "Credential=assumed-key/" in origin.requests[1][1]["authorization"]


async def test_read_deadline_survives_custom_transport(lab: Lab) -> None:
    origin, proxy = lab.start(), lab.start(proxy=True)
    origin.gate = threading.Event()
    with pytest.raises(TimeoutError):
        await client(
            origin.endpoint, proxy_url=proxy.endpoint, read_timeout=0.1,
        ).get_caller_identity()
    assert len(proxy.requests) == len(origin.requests) == 1


@pytest.mark.parametrize("contents", [None, b"", b"not a certificate", (
    b"-----BEGIN CERTIFICATE-----\nYWJjZA==\n-----END CERTIFICATE-----"
)])
async def test_invalid_bundle_is_typed_and_logged_without_contents(
    lab: Lab, tmp_path: Path, contents: bytes | None, json_logging: io.StringIO,
) -> None:
    path = tmp_path / "invalid.pem"
    if contents is not None:
        path.write_bytes(contents)
    origin = lab.start()
    configure_logging(stream=json_logging)
    with pytest.raises(ConfigurationError):
        await client(origin.endpoint, ca_bundle=str(path)).get_caller_identity()
    record = json.loads(json_logging.getvalue())
    assert record["error_type"] == "ConfigurationError"
    assert str(path) not in json_logging.getvalue()
    assert "source-secret" not in json_logging.getvalue()
    assert not origin.requests
