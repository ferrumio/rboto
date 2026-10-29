"""Exercise the installed wheels without importing checkout sources or contacting AWS."""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
from importlib.metadata import distribution
from pathlib import Path
from typing import cast


def smoke(catalog_path: Path) -> None:
    catalog = json.loads(catalog_path.read_text())
    version = catalog["version"]
    services = cast(list[dict[str, str]], catalog["services"])
    if not services:
        raise ValueError("release catalog contains no services")
    facade = importlib.import_module("rboto")
    assert distribution("rboto").version == version
    assert facade.__version__ == version
    for service in services:
        installed = distribution(service["distribution"])
        assert installed.version == version, service["distribution"]
        files = {str(path) for path in installed.files or ()}
        package = service["module"]
        for filename in ("py.typed", "_native.pyi", "client.py", "types.py"):
            assert f"{package}/{filename}" in files, (package, filename)
        native = importlib.import_module(f"{package}._native")
        module = importlib.import_module(package)
        client_class = getattr(facade, service["client"])
        assert client_class is getattr(module, service["client"])
        client = client_class(region="us-east-1", endpoint_url="http://127.0.0.1:9")
        methods = [
            name for name, method in inspect.getmembers(client_class)
            if not name.startswith("_") and inspect.iscoroutinefunction(method)
        ]
        assert methods, service["client"]
        for name in methods:
            assert callable(getattr(client._native, name)), (package, name)
        assert native.__file__ is not None
        print(f"{service['distribution']} {version}: {len(methods)} async operations")
    print(f"Validated {len(services)} installed service wheels.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    args = parser.parse_args()
    smoke(args.catalog)


if __name__ == "__main__":
    main()
