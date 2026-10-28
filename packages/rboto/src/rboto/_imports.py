"""Import optional service packages without hiding broken installations."""

from importlib import import_module
from types import ModuleType


def import_service(package: str, extra: str) -> ModuleType:
    try:
        return import_module(package)
    except ModuleNotFoundError as error:
        if error.name != package:
            raise
        raise ImportError(
            f'{extra} support is not installed. Install it with: pip install "rboto[{extra}]"'
        ) from error
