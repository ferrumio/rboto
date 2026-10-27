"""Exercise the native CLI through real SDK transports and local protocol fixtures."""

import base64
import json
import os
import selectors
import signal
import ssl
import struct
import subprocess
import threading
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest


def event_frame(event: str, payload: dict[str, object], *, exception: bool = False) -> bytes:
    headers = bytearray()
    for name, value in {
        ":message-type": "exception" if exception else "event",
        ":exception-type" if exception else ":event-type": event,
        ":content-type": "application/json",
    }.items():
        key, text = name.encode(), value.encode()
        headers += bytes([len(key)]) + key + b"\x07" + struct.pack(">H", len(text)) + text
    body = json.dumps(payload).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    message = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


@pytest.fixture(scope="session")
def binary() -> Path:
    value = os.environ.get("RBOTO_CLI_BINARY")
    if not value:
        pytest.skip("set RBOTO_CLI_BINARY to the compiled native CLI")
    path = Path(value).resolve()
    assert path.is_file(), path
    return path


@dataclass
class Peer:
    endpoint: str = ""
    body: bytes = b"{}"
    status: int = 200
    content_type: str = "application/json"
    truncate: bool = False
    duplex: bool = False
    pages: list[bytes] = field(default_factory=list)
    requests: list[tuple[str, bytes]] = field(default_factory=list)
    request_received: threading.Event = field(default_factory=threading.Event)
    gate: threading.Event | None = None
    environment: dict[str, str] = field(default_factory=dict)


@pytest.fixture
def peer(tmp_path: Path) -> Iterator[Peer]:
    certificate, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-addext",
            "basicConstraints=critical,CA:FALSE",
            "-addext",
            "extendedKeyUsage=serverAuth",
        ],
        check=True,
        capture_output=True,
    )
    config = Peer()
    config.environment = {
        name: value for name, value in os.environ.items() if not name.startswith("AWS_")
    }
    config.environment.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_MAX_ATTEMPTS="1",
        AWS_CONFIG_FILE="/dev/null",
        AWS_SHARED_CREDENTIALS_FILE="/dev/null",
        SSL_CERT_FILE=str(certificate),
        SSL_CERT_DIR=str(tmp_path / "empty-certs"),
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle(self) -> None:
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                # Cancellation and TLS rejection intentionally close fixture sockets.
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

        def body(self) -> bytes:
            if self.headers.get("Transfer-Encoding") != "chunked":
                return self.rfile.read(int(self.headers.get("Content-Length", "0")))
            chunks = []
            while line := self.rfile.readline():
                size = int(line.split(b";")[0], 16)
                if not size:
                    while self.rfile.readline() not in {b"\r\n", b""}:
                        pass
                    break
                chunks.append(self.rfile.read(size))
                assert self.rfile.read(2) == b"\r\n"
            return b"".join(chunks)

        def chunk(self, payload: bytes) -> None:
            self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
            self.wfile.flush()

        def respond(self) -> None:
            assert "Credential=testing/" in self.headers.get("Authorization", "")
            if config.duplex:
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.amazon.eventstream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.chunk(event_frame("chunk", {"bytes": base64.b64encode(b"ready").decode()}))
                config.requests.append((self.path, self.body()))
                self.chunk(event_frame("chunk", {"bytes": base64.b64encode(b"done").decode()}))
                self.chunk(b"")
                return
            config.requests.append((self.path, self.body()))
            config.request_received.set()
            if config.gate is not None and not config.gate.wait(timeout=10):
                return
            body = config.pages.pop(0) if config.pages else config.body
            self.send_response(config.status)
            self.send_header("Content-Type", config.content_type)
            self.send_header("Content-Length", str(len(body) + (1024 if config.truncate else 0)))
            self.send_header("x-amzn-requestid", "cli-fixture")
            self.send_header("x-amz-request-id", "cli-fixture")
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            if config.truncate:
                self.close_connection = True

        do_GET = respond
        do_POST = respond
        do_PUT = respond
        do_DELETE = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    config.endpoint = f"https://localhost:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield config
    finally:
        if config.gate is not None:
            config.gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def command(binary: Path, peer: Peer, service: str, operation: str, *args: str) -> list[str]:
    return [
        str(binary),
        service,
        operation,
        "--region",
        "us-east-1",
        "--endpoint-url",
        peer.endpoint,
        *args,
    ]


def invoke(
    binary: Path,
    peer: Peer,
    service: str,
    operation: str,
    params: dict[str, object] | None = None,
    *args: str,
    expected: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[Any]]:
    result = subprocess.run(
        command(binary, peer, service, operation, "--input", json.dumps(params or {}), *args),
        env=peer.environment,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == expected, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result, records


def test_catalog_and_generated_method_contract(binary: Path) -> None:
    result = subprocess.run(
        [str(binary), "--self-test"], text=True, capture_output=True, check=True
    )
    catalog = json.loads((Path(__file__).parents[2] / "cli/app/src/catalog.json").read_text())
    assert json.loads(result.stdout) == {
        "services": len(catalog),
        "operations": sum(len(row["operations"]) for row in catalog),
    }
    result = subprocess.run(
        [str(binary), "s3", "get-object", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "--bucket" in result.stdout and "--key" in result.stdout


def test_nested_documents_and_tagged_unions(binary: Path, peer: Peer) -> None:
    document = {"nested": [None, True, 42, -7, 0.25, {"value": "hello"}]}
    peer.body = json.dumps(
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "Hello"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
            "metrics": {"latencyMs": 1},
            "additionalModelResponseFields": document,
        }
    ).encode()
    _, rows = invoke(
        binary,
        peer,
        "bedrock-runtime",
        "converse",
        {
            "model_id": "example",
            "messages": [{"role": "user", "content": [{"text": "hello"}]}],
            "additional_model_request_fields": document,
            "inference_config": {"temperature": 0.5},
        },
    )
    assert rows[0]["additional_model_response_fields"] == document
    assert rows[0]["usage"]["input_tokens"] == 2
    assert json.loads(peer.requests[0][1])["additionalModelRequestFields"] == document


def test_recursive_inputs_and_sparse_outputs(binary: Path, peer: Peer) -> None:
    peer.body = json.dumps(
        {
            "systemMessage": "answer",
            "sourceAttributions": [None, {"title": "Example"}],
        }
    ).encode()
    _, rows = invoke(
        binary,
        peer,
        "qbusiness",
        "chat-sync",
        {
            "application_id": "example",
            "user_message": "hello",
            "attribute_filter": {
                "not_filter": {
                    "not_filter": {
                        "equals_to": {"name": "kind", "value": {"string_value": "test"}},
                    }
                }
            },
        },
    )
    assert rows[0]["source_attributions"][0] is None
    assert rows[0]["source_attributions"][1]["title"] == "Example"
    assert json.loads(peer.requests[0][1])["attributeFilter"]["notFilter"]["notFilter"] == {
        "equalsTo": {"name": "kind", "value": {"stringValue": "test"}},
    }


def test_unit_union_and_keyword_member(binary: Path, peer: Peer) -> None:
    invoke(
        binary,
        peer,
        "bedrock-agentcore-control",
        "create-harness",
        {
            "harness_name": "example",
            "execution_role_arn": "arn:aws:iam::123456789012:role/example",
            "tools": [
                {
                    "type": "agentcore_gateway",
                    "config": {
                        "agent_core_gateway": {
                            "gateway_arn": (
                                "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/example"
                            ),
                            "outbound_auth": {"aws_iam": None},
                        }
                    },
                }
            ],
        },
    )
    tool = json.loads(peer.requests[0][1])["tools"][0]
    assert tool["type"] == "agentcore_gateway"
    assert tool["config"]["agentCoreGateway"]["outboundAuth"] == {"awsIam": {}}


def test_pagination_and_explicit_page_limit(binary: Path, peer: Peer) -> None:
    peer.content_type = "text/xml"
    first = (
        b'<ListTopicsResponse xmlns="http://sns.amazonaws.com/doc/2010-03-31/">'
        b"<ListTopicsResult><Topics/><NextToken>next</NextToken></ListTopicsResult>"
        b"</ListTopicsResponse>"
    )
    last = (
        b'<ListTopicsResponse xmlns="http://sns.amazonaws.com/doc/2010-03-31/">'
        b"<ListTopicsResult><Topics/></ListTopicsResult></ListTopicsResponse>"
    )
    peer.pages = [first, last]
    _, rows = invoke(binary, peer, "sns", "list-topics", {}, "--paginate")
    assert len(rows) == 2 and b"NextToken=next" in peer.requests[1][1]
    peer.pages = [first, last]
    before = len(peer.requests)
    _, rows = invoke(binary, peer, "sns", "list-topics", {}, "--max-pages", "1")
    assert len(rows) == 1 and len(peer.requests) == before + 1


def test_binary_input_and_output(binary: Path, peer: Peer, tmp_path: Path) -> None:
    source, destination = tmp_path / "input", tmp_path / "response"
    source.write_bytes(b"\x00\xffrequest")
    peer.body = b"\x00\xffresponse"
    _, rows = invoke(
        binary,
        peer,
        "lambda",
        "invoke",
        {"function_name": "example"},
        "--bytes",
        f"payload={source}",
        "--output-file",
        str(destination),
    )
    assert peer.requests[0][1] == source.read_bytes()
    assert destination.read_bytes() == peer.body
    assert rows[0]["payload"]["bytes"] == len(peer.body)


def test_interrupted_download_preserves_destination(
    binary: Path,
    peer: Peer,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "download"
    destination.write_bytes(b"previous contents")
    peer.body = b"partial response"
    peer.truncate = True
    result, _ = invoke(
        binary,
        peer,
        "s3",
        "get-object",
        {"bucket": "example", "key": "key"},
        "--output-file",
        str(destination),
        expected=1,
    )
    assert json.loads(result.stderr)["error"] == "StreamError"
    assert destination.read_bytes() == b"previous contents"
    assert not list(tmp_path.glob(".rboto-*"))
    peer.truncate = False
    _, rows = invoke(
        binary,
        peer,
        "s3",
        "get-object",
        {"bucket": "example", "key": "key"},
        "--output-file",
        str(destination),
    )
    assert destination.read_bytes() == peer.body
    assert rows[0]["body"]["bytes"] == len(peer.body)


def test_modeled_error_and_request_id(binary: Path, peer: Peer) -> None:
    peer.status = 400
    peer.body = b'{"__type":"ResourceNotFoundException","Message":"missing secret"}'
    result, _ = invoke(
        binary,
        peer,
        "secrets-manager",
        "get-secret-value",
        {"secret_id": "missing"},
        expected=1,
    )
    error = json.loads(result.stderr)
    assert error["error"] == "ResourceNotFoundException"
    assert error["request_id"] == "cli-fixture"
    assert error["service"] == "secrets-manager"


def test_event_stream_exception_keeps_modeled_code(binary: Path, peer: Peer) -> None:
    peer.content_type = "application/vnd.amazon.eventstream"
    peer.body = event_frame("validationException", {"message": "invalid stream"}, exception=True)
    result, rows = invoke(
        binary,
        peer,
        "bedrock-runtime",
        "invoke-model-with-response-stream",
        {"model_id": "example", "body": base64.b64encode(b"{}").decode()},
        expected=1,
    )
    assert rows[0]["body"] == {"stream": "events"}
    assert json.loads(result.stderr)["error"] == "ValidationException"


def test_duplex_stream_receives_before_stdin_is_closed(binary: Path, peer: Peer) -> None:
    peer.duplex = True
    process = subprocess.Popen(
        command(
            binary,
            peer,
            "bedrock-runtime",
            "invoke-model-with-bidirectional-stream",
            "--model-id",
            "example",
            "--events",
            "body=-",
        ),
        env=peer.environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        process.stdin.write(
            json.dumps({"chunk": {"bytes": base64.b64encode(b"first").decode()}}) + "\n"
        )
        process.stdin.flush()
        records = []
        pending = b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while len(records) < 2:
                assert selector.select(timeout=10), "CLI did not produce a live event"
                chunk = os.read(process.stdout.fileno(), 65536)
                assert chunk, "CLI closed stdout before the first event"
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    records.append(json.loads(line))
        assert pending == b""
        assert records[1]["event"]["chunk"]["bytes"] == base64.b64encode(b"ready").decode()
        process.stdin.write(
            json.dumps({"chunk": {"bytes": base64.b64encode(b"second").decode()}}) + "\n"
        )
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["event"]["chunk"]["bytes"] == base64.b64encode(b"done").decode()
        assert base64.b64encode(b"second") in peer.requests[0][1]
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_invalid_inputs_fail_without_http(binary: Path, peer: Peer) -> None:
    invoke(binary, peer, "lambda", "invoke", {"function_nam": "typo"}, expected=2)
    invoke(
        binary,
        peer,
        "bedrock-runtime",
        "converse",
        {
            "model_id": "example",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": "hello", "image": {}},
                    ],
                }
            ],
        },
        expected=2,
    )
    assert peer.requests == []


def test_s3_upload_reads_a_byte_stream_from_file(binary: Path, peer: Peer, tmp_path: Path) -> None:
    source = tmp_path / "upload"
    source.write_bytes(b"streamed upload" * 1024)
    peer.body = b""
    invoke(
        binary,
        peer,
        "s3",
        "put-object",
        {"bucket": "example", "key": "file"},
        "--bytes",
        f"body={source}",
    )
    assert source.read_bytes() in peer.requests[0][1]


def test_binary_stdout_separates_metadata(binary: Path, peer: Peer) -> None:
    peer.body = b"\x00\xffbinary"
    result = subprocess.run(
        command(
            binary,
            peer,
            "s3",
            "get-object",
            "--bucket",
            "example",
            "--key",
            "file",
            "--output-file",
            "-",
        ),
        env=peer.environment,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == peer.body
    assert json.loads(result.stderr)["body"]["bytes"] == len(peer.body)


def test_profile_credentials(binary: Path, peer: Peer, tmp_path: Path) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text("[local]\naws_access_key_id=testing\naws_secret_access_key=testing\n")
    peer.environment.pop("AWS_ACCESS_KEY_ID")
    peer.environment.pop("AWS_SECRET_ACCESS_KEY")
    peer.environment["AWS_SHARED_CREDENTIALS_FILE"] = str(credentials)
    invoke(binary, peer, "lambda", "invoke", {"function_name": "example"}, "--profile", "local")
    assert len(peer.requests) == 1


def test_untrusted_tls_is_rejected(binary: Path, peer: Peer, tmp_path: Path) -> None:
    # The fixture certificate is trusted only via this explicit CA file.
    peer.environment["SSL_CERT_FILE"] = str(tmp_path / "missing-ca.pem")
    result, _ = invoke(
        binary,
        peer,
        "lambda",
        "invoke",
        {"function_name": "example"},
        expected=1,
    )
    assert json.loads(result.stderr)["error"] == "DispatchError"
    assert peer.requests == []


def test_deadline_stops_a_waiting_request(binary: Path, peer: Peer) -> None:
    peer.gate = threading.Event()
    result, _ = invoke(binary, peer, "sns", "list-topics", {}, "--timeout", "1", expected=1)
    assert json.loads(result.stderr)["error"] == "TimeoutError"


def test_cancellation_stops_a_waiting_request(binary: Path, peer: Peer) -> None:
    peer.gate = threading.Event()
    process = subprocess.Popen(
        command(binary, peer, "sns", "list-topics"),
        env=peer.environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert peer.request_received.wait(timeout=10)
        process.send_signal(signal.SIGINT)
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 130
        assert json.loads(stderr)["error"] == "Interrupted"
    finally:
        if process.poll() is None:
            process.kill()
        peer.gate.set()
        process.wait(timeout=5)
