"""Verify the packaged HTTPS client and credential providers against a local TLS peer."""

import json
import os
import ssl
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SELECTED = set(filter(None, os.environ.get("RBOTO_TEST_SERVICES", "").split(",")))
pytestmark = pytest.mark.skipif(bool(SELECTED and "sns" not in SELECTED), reason="SNS not selected")


@pytest.fixture
def tls_peer(tmp_path: Path) -> Iterator[tuple[str, Path, list[str]]]:
    certificate = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(certificate), "-days", "1",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "extendedKeyUsage=serverAuth",
        ],
        check=True, capture_output=True,
    )
    authorizations: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            if self.path != "/credentials" or self.headers.get("Authorization") != "local-token":
                self.send_error(403)
                return
            body = json.dumps(
                {
                    "AccessKeyId": "testing", "SecretAccessKey": "testing",
                    "Token": "testing-token", "Expiration": "2099-01-01T00:00:00Z",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            authorizations.append(self.headers.get("Authorization", ""))
            body = (
                '<ListTopicsResponse xmlns="http://sns.amazonaws.com/doc/2010-03-31/">'
                "<ListTopicsResult><Topics/></ListTopicsResult>"
                "<ResponseMetadata><RequestId>local-test</RequestId></ResponseMetadata>"
                "</ListTopicsResponse>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", certificate, authorizations
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("credentials", ["environment", "profile", "process", "container"])
def test_https_preserves_credential_providers(
    tls_peer: tuple[str, Path, list[str]], tmp_path: Path, credentials: str
) -> None:
    endpoint, certificate, authorizations = tls_peer
    env = os.environ.copy()
    for name in (
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    ):
        env.pop(name, None)
    env.update(
        AWS_EC2_METADATA_DISABLED="true", AWS_MAX_ATTEMPTS="1",
        AWS_CONFIG_FILE=str(tmp_path / "config"),
        AWS_SHARED_CREDENTIALS_FILE=str(tmp_path / "credentials"),
        SSL_CERT_FILE=str(certificate), SSL_CERT_DIR=str(tmp_path / "empty-certs"),
    )
    if credentials == "environment":
        env.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing")
    elif credentials == "container":
        env.update(
            AWS_CONTAINER_CREDENTIALS_FULL_URI=f"{endpoint}/credentials",
            AWS_CONTAINER_AUTHORIZATION_TOKEN="local-token",
        )
    else:
        env["AWS_PROFILE"] = "local-test"
        if credentials == "profile":
            (tmp_path / "credentials").write_text(
                "[local-test]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
            )
        else:
            helper = tmp_path / "credentials.py"
            helper.write_text(
                'print(\'{"Version":1,"AccessKeyId":"testing","SecretAccessKey":"testing"}\')\n'
            )
            (tmp_path / "config").write_text(
                f'[profile local-test]\ncredential_process = "{sys.executable}" "{helper}"\n'
            )
    worker = """
import asyncio
import sys
from rboto import sns
async def main():
    result = await sns(region="us-east-1", endpoint_url=sys.argv[1]).list_topics()
    assert result.topics == []
    await asyncio.sleep(0.2)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", worker, endpoint], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert len(authorizations) == 1
    assert "Credential=testing/" in authorizations[0]


def test_https_rejects_an_untrusted_certificate(
    tls_peer: tuple[str, Path, list[str]], tmp_path: Path
) -> None:
    endpoint, _, authorizations = tls_peer
    unrelated_certificate = tmp_path / "unrelated-ca.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(tmp_path / "unrelated-key.pem"),
            "-out", str(unrelated_certificate), "-days", "1",
            "-subj", "/CN=Unrelated test CA",
            "-addext", "basicConstraints=critical,CA:TRUE",
        ],
        check=True, capture_output=True,
    )
    env = os.environ.copy()
    env.update(
        AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
        AWS_EC2_METADATA_DISABLED="true", AWS_MAX_ATTEMPTS="1",
        SSL_CERT_FILE=str(unrelated_certificate),
        SSL_CERT_DIR=str(tmp_path / "missing-certs"),
    )
    worker = """
import asyncio
import sys
from rboto import sns
from rboto_sns.exceptions import SNSError
async def main():
    try:
        await sns(region="us-east-1", endpoint_url=sys.argv[1]).list_topics()
    except SNSError as error:
        message = str(error).lower()
        assert "certificate" in message and "unknownissuer" in message, str(error)
    else:
        raise AssertionError("untrusted TLS certificate was accepted")
    await asyncio.sleep(0.2)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", worker, endpoint], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert authorizations == []
