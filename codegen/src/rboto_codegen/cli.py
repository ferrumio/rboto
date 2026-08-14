import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

from .alignment import align
from .generator import generate_service
from .registry import get_service, list_services
from .rust_crate import parse_crate
from .smithy import fetch_model, load_model


def _parser_error(parser: argparse.ArgumentParser, message: str) -> NoReturn:
    parser.error(message)


def _add_service_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("service_id", nargs="?")
    parser.add_argument("--all", action="store_true", dest="all_services")


def main() -> None:
    parser = argparse.ArgumentParser(prog="rboto-codegen")
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="list registered AWS services")
    list_parser.add_argument("--json", action="store_true")
    describe = subparsers.add_parser("describe", help="show a service descriptor")
    describe.add_argument("service_id")
    fetch = subparsers.add_parser("fetch-model", help="download and verify a Smithy model")
    _add_service_selection(fetch)
    report = subparsers.add_parser("report", help="compare Smithy and Rust crate APIs")
    _add_service_selection(report)
    generate = subparsers.add_parser("generate", help="generate a complete service adapter")
    _add_service_selection(generate)
    args = parser.parse_args()

    if args.command == "list":
        services = [descriptor.service_id for descriptor in list_services()]
        if args.json:
            print(json.dumps(services))
        else:
            for service_id in services:
                print(service_id)
        return

    if args.command == "describe":
        try:
            descriptor = get_service(args.service_id)
        except KeyError as error:
            _parser_error(parser, str(error))
        print(json.dumps(asdict(descriptor), indent=2, sort_keys=True))
        return

    if args.all_services:
        descriptors = list_services()
    elif args.service_id:
        descriptors = (get_service(args.service_id),)
    else:
        _parser_error(parser, "provide a service_id or --all")

    repository_root = Path(__file__).parents[3]
    for descriptor in descriptors:
        model_path = Path(__file__).parents[2] / "models" / f"{descriptor.service_id}.json"
        if args.command == "fetch-model":
            print(fetch_model(descriptor, model_path))
            continue

        if not model_path.exists():
            fetch_model(descriptor, model_path)

        if args.command == "report":
            service = load_model(model_path, descriptor)
            crate = parse_crate(descriptor)
            result = align(service, crate)
            print(f"[{descriptor.service_id}]")
            print(f"smithy operations: {len(service.operations)}")
            print(f"rust operations: {len(crate.operations)}")
            print(f"aligned operations: {len(result.aligned)}")
            print(f"mismatched operations: {len(result.mismatched)}")
            print(f"smithy-only operations: {len(result.smithy_only_operations)}")
            print(f"rust-only operations: {len(result.rust_only_operations)}")
            for mismatch in result.mismatched:
                print(f"mismatch {mismatch.name}: {mismatch}")
            if result.smithy_only_operations or result.rust_only_operations:
                raise RuntimeError(
                    f"operation alignment failed for {descriptor.service_id}"
                )
            continue

        if args.command == "generate":
            generated = generate_service(descriptor, model_path, repository_root)
            for path in (
                generated.facade,
                generated.core_init,
                generated.cargo,
                generated.pyproject,
                generated.package_init,
                generated.py_typed,
                generated.rust_runtime,
                generated.rust,
                generated.client,
                generated.types,
                generated.native_stub,
                generated.exceptions,
            ):
                print(path)
