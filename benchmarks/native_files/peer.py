"""Controlled S3-compatible HTTP peer; separate process from measured clients."""
import argparse
import asyncio
import base64
import hashlib
import json
import zlib
from pathlib import Path

from aiohttp import web

BLOCK = bytes(range(256)) * 256
RECORDS = []
ATTEMPTS = {}


async def handle(request):
    key = request.match_info["key"]
    length = int(key.split("-")[0])
    attempt = ATTEMPTS[key] = ATTEMPTS.get(key, 0) + 1
    record = {"key": key, "method": request.method, "attempt": attempt, "complete": False}
    record["headers"] = {
        name.lower(): value for name, value in request.headers.items()
        if name.lower() in ("content-encoding", "transfer-encoding", "content-length")
        or "checksum" in name.lower() or name.lower() == "x-amz-trailer"
    }
    RECORDS.append(record)
    if request.method == "PUT":
        digest = hashlib.sha256()
        size = 0
        crc = 0
        async def incoming():
            if "aws-chunked" not in request.headers.get("Content-Encoding", ""):
                async for chunk in request.content.iter_chunked(1024 * 1024):
                    yield chunk
                return
            while True:
                line = await request.content.readline()
                n = int(line.strip().split(b";")[0], 16)
                if not n:
                    while line := (await request.content.readline()).strip():
                        name, value = line.decode().split(":", 1)
                        record[name.lower()] = value.strip()
                    return
                remaining = n
                while remaining:
                    chunk = await request.content.readexactly(min(remaining, 1024 * 1024))
                    remaining -= len(chunk)
                    yield chunk
                assert await request.content.readexactly(2) == b"\r\n"
        try:
            async for chunk in incoming():
                digest.update(chunk)
                crc = zlib.crc32(chunk, crc)
                size += len(chunk)
                if "slow" in key:
                    await asyncio.sleep(0.025)
            record.update(size=size, sha256=digest.hexdigest(), complete=True)
            expected_crc = (
                record.get("x-amz-checksum-crc32") or request.headers.get("x-amz-checksum-crc32")
            )
            if expected_crc:
                assert expected_crc == base64.b64encode(crc.to_bytes(4, "big")).decode()
                record["crc32_verified"] = True
        except Exception as error:
            record.update(size=size, error=type(error).__name__)
            raise
        if "retry" in key and attempt == 1:
            return web.Response(
                status=503,
                body=b"<Error><Code>SlowDown</Code><Message>retry</Message></Error>",
                content_type="application/xml",
            )
        return web.Response(headers={"ETag": '"opaque-etag"', "x-amz-request-id": "local"})
    if request.method == "GET":
        response = web.StreamResponse(headers={
            "Content-Length": str(length),
            "Content-Type": "application/octet-stream",
            "x-amz-request-id": "local",
        })
        await response.prepare(request)
        size = 0
        try:
            while size < length:
                chunk = BLOCK[:min(len(BLOCK), length - size)]
                await response.write(chunk)
                size += len(chunk)
                if "slow" in key:
                    await asyncio.sleep(0.025)
                if "truncate" in key and size >= length // 2:
                    request.transport.close()
                    return response
            await response.write_eof()
            record.update(size=size, complete=True)
        except (ConnectionError, RuntimeError) as error:
            record.update(size=size, error=type(error).__name__)
        return response
    return web.Response(status=405)


async def metrics(request):
    return web.json_response(RECORDS)


async def main(path):
    app = web.Application(client_max_size=1024**4)
    app.router.add_get("/_metrics", metrics)
    app.router.add_route("*", "/{bucket}/{key}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    temporary = Path(path).with_suffix(".tmp")
    temporary.write_text(json.dumps({"endpoint": f"http://127.0.0.1:{port}"}))
    temporary.replace(path)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", required=True)
    asyncio.run(main(parser.parse_args().ready))
