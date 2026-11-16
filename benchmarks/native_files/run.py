"""Run randomized, isolated file-streaming comparisons against a local peer."""

import argparse
import hashlib
import itertools
import json
import random
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BLOCK = bytes(range(256)) * 4096


def measure(args, directory, output, endpoint):
    sizes = [int(value) * 1024**2 for value in args.sizes_mib.split(",")]
    if any(size <= 0 or size > 1024**3 for size in sizes):
        raise ValueError("sizes must be between 1 and 1024 MiB")
    files = {}
    for size in sizes:
        path = directory / f"source-{size}.bin"
        digest = hashlib.sha256()
        with path.open("xb") as stream:
            for _ in range(size // len(BLOCK)):
                stream.write(BLOCK)
                digest.update(BLOCK)
        files[size] = (path, digest.hexdigest())
    scenarios = [
        (*scenario, mode)
        for scenario in itertools.product(
            range(args.rounds), args.backends.split(","), sizes, [1, 4], ["upload", "download"],
        )
        for mode in (
            ["bytes", "python", "native"] if scenario[-1] == "upload"
            else ["python", "python-batched", "native"]
        )
    ]
    random.Random(20261115).shuffle(scenarios)
    with output.open("x") as stream:
        for index, (round_, backend, size, concurrency, direction, mode) in enumerate(scenarios, 1):
            path, digest = files[size]
            command = [
                sys.executable, str(ROOT / "worker.py"),
                "--endpoint", endpoint, "--source", str(path), "--directory", str(directory),
                "--sha256", digest, "--size", str(size), "--concurrency", str(concurrency),
                "--direction", direction, "--mode", mode, "--backend", backend,
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise RuntimeError(f"{command}\n{result.stdout}\n{result.stderr}")
            record = json.loads(result.stdout)
            if direction == "upload":
                with urllib.request.urlopen(endpoint + "/_metrics") as response:
                    receipts = {item["key"]: item for item in json.load(response)}
                for item in record["results"]:
                    receipt = receipts[item["key"]]
                    assert receipt["complete"] and receipt["size"] == size
                    assert receipt["sha256"] == digest, receipt
                    item["sha256"] = receipt["sha256"]
            record["round"] = round_
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            print(
                f"{index}/{len(scenarios)} {backend} {direction} {mode} "
                f"{size//1024**2}MiB x{concurrency}: "
                f"wall={record['wall_s']:.3f}s cpu={record['cpu_s']:.3f}s "
                f"rss+={record['rss_growth']/1024**2:.1f}MiB "
                f"gap={record['loop_gap_max_ms']:.2f}ms",
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path, help="Parent on a disk filesystem")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--sizes-mib", default="1,32,128")
    parser.add_argument("--backends", default="asyncio,trio")
    args = parser.parse_args()
    if args.rounds < 1 or not set(args.backends.split(",")) <= {"asyncio", "trio"}:
        parser.error("use positive rounds and asyncio/trio backends")
    args.output.mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    if (args.output / "results.jsonl").exists():
        parser.error("results.jsonl already exists; choose a new output directory")
    with tempfile.TemporaryDirectory(prefix="rboto-files-", dir=args.data_dir) as temporary:
        directory = Path(temporary)
        ready = directory / "peer.json"
        with (args.output / "peer.log").open("w") as log:
            peer = subprocess.Popen(
                [sys.executable, str(ROOT / "peer.py"), "--ready", str(ready)],
                stdout=log, stderr=log,
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.exists():
                    if peer.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("peer failed to start; inspect peer.log")
                    time.sleep(.02)
                endpoint = json.loads(ready.read_text())["endpoint"]
                measure(args, directory, args.output / "results.jsonl", endpoint)
            finally:
                peer.terminate()
                try:
                    peer.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    peer.kill()
                    peer.wait()


if __name__ == "__main__":
    main()
