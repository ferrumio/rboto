"""Reject interpreter caches and keep the native and typing payload in service wheels."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile


def check_wheel(path: Path) -> None:
    with ZipFile(path) as wheel:
        names = wheel.namelist()
        unwanted = [
            name for name in names
            if "__pycache__" in Path(name).parts or name.endswith((".pyc", ".pyo"))
        ]
        if unwanted:
            raise ValueError(f"{path.name}: interpreter caches must not be packaged: {unwanted}")
        native = [name for name in names if name.endswith((".so", ".pyd"))]
        if len(native) != 1:
            raise ValueError(f"{path.name}: expected one native extension, found {native}")
        package = Path(native[0]).parent.as_posix()
        required_files = (
            "__init__.py", "client.py", "types.py", "exceptions.py", "_native.pyi", "py.typed",
        )
        for required in required_files:
            if f"{package}/{required}" not in names:
                raise ValueError(f"{path.name}: missing {required}")
        if wheel.testzip() is not None:
            raise ValueError(f"{path.name}: corrupt wheel")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.wheels:
        check_wheel(path)
        print(f"{path.name}: native payload and typing present; no interpreter caches")


if __name__ == "__main__":
    main()
