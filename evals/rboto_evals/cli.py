from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .container import prepare
from .providers import Bedrock, DockerGrader
from .runner import Config, plan, regrade, run


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in rboto code-generation evaluation")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--sudo", action="store_true", help="use sudo -n docker")
    commands = parser.add_subparsers(dest="command", required=True)
    image = commands.add_parser("prepare", help="snapshot already-installed native clients")
    image.add_argument("--directory", type=Path, required=True)
    image.add_argument("--tag", default="rboto-agent-evals:local")
    image.add_argument("--version", required=True)
    replay = commands.add_parser("regrade", help="diagnose stored code without model calls")
    replay.add_argument("--source", type=Path, required=True)
    replay.add_argument("--manifest", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    replay.add_argument("--canonical-dependencies", action="store_true")
    experiment = commands.add_parser("run", help="print a plan; use --live to call the model")
    experiment.add_argument("--live", action="store_true")
    experiment.add_argument("--model", required=True)
    experiment.add_argument("--region", default="eu-west-2")
    experiment.add_argument("--version", required=True)
    experiment.add_argument("--repeats", type=int, default=2)
    experiment.add_argument("--max-calls", type=int, required=True)
    experiment.add_argument("--max-output-tokens", type=int, required=True)
    experiment.add_argument("--deadline-seconds", type=int, required=True)
    experiment.add_argument("--manifest", type=Path)
    experiment.add_argument("--output", type=Path)
    args = parser.parse_args()
    docker = ["sudo", "-n", "docker"] if args.sudo else ["docker"]
    if args.command == "regrade":
        manifest = json.loads(args.manifest.read_text())
        source = json.loads((args.source / "results.json").read_text())
        version = source["config"]["version"]
        if manifest["sdk_version"] != version:
            parser.error("manifest SDK version differs from recorded version")
        result = regrade(
            args.source,
            args.output,
            DockerGrader(docker, manifest["image_id"], version),
            manifest,
            canonical_dependencies=args.canonical_dependencies,
        )
        print(json.dumps({"cases": len(result["cases"]), "output": str(args.output)}))
        return
    if args.command == "prepare":
        manifest = prepare(
            args.root.resolve(),
            args.directory.resolve(),
            args.version,
            docker,
            args.tag,
        )
        print(
            json.dumps(
                {
                    "image_id": manifest["image_id"],
                    "manifest": str(args.directory / "manifest.json"),
                }
            )
        )
        return
    config = Config(
        model=args.model,
        region=args.region,
        version=args.version,
        repeats=args.repeats,
        max_calls=args.max_calls,
        max_output_tokens=args.max_output_tokens,
        deadline_seconds=args.deadline_seconds,
    )
    config.validate()
    if not args.live:
        print(
            json.dumps(
                {
                    "runs": plan(config),
                    "maximum_calls": config.max_calls,
                    "maximum_output_tokens": config.max_calls * config.max_output_tokens,
                },
                indent=2,
            )
        )
        return
    if args.manifest is None or args.output is None:
        parser.error("--live requires --manifest and --output")
    manifest = json.loads(args.manifest.read_text())
    if manifest["sdk_version"] != config.version:
        parser.error("manifest SDK version differs from requested version")
    manifest["aws_cli"] = subprocess.check_output(["aws", "--version"], text=True).strip()
    result = run(
        args.root.resolve(),
        args.output.resolve(),
        config,
        Bedrock(config),
        DockerGrader(docker, manifest["image_id"], config.version),
        manifest,
    )
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
