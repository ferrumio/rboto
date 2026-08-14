from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

from .model import ServiceDescriptor


def service_config_dir() -> Path:
    return Path(__file__).parent / "services"


def _string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise ValueError(f"service configuration field {name!r} must be a string")
    return value


def _optional_string(data: dict[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"service configuration field {name!r} must be a string")
    return value


def _boolean(data: dict[str, object], name: str) -> bool:
    value = data.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"service configuration field {name!r} must be a boolean")
    return value


def load_service(path: Path) -> ServiceDescriptor:
    data = cast(dict[str, object], tomllib.loads(path.read_text()))
    descriptor = ServiceDescriptor(
        service_id=_string(data, "service_id"),
        python_name=_string(data, "python_name"),
        distribution_name=_string(data, "distribution_name"),
        python_package=_string(data, "python_package"),
        client_class=_string(data, "client_class"),
        adapter_crate=_string(data, "adapter_crate"),
        rust_crate=_string(data, "rust_crate"),
        rust_module=_string(data, "rust_module"),
        rust_crate_version=_string(data, "rust_crate_version"),
        model_url=_string(data, "model_url"),
        model_sha256=_string(data, "model_sha256"),
        aws_model_hash=_string(data, "aws_model_hash"),
        smithy_codegen_revision=_string(data, "smithy_codegen_revision"),
        customization=_optional_string(data, "customization"),
        force_path_style_for_custom_endpoint=_boolean(
            data, "force_path_style_for_custom_endpoint"
        ),
    )
    if path.stem != descriptor.service_id:
        raise ValueError(
            f"service configuration filename {path.stem!r} does not match "
            f"service_id {descriptor.service_id!r}"
        )
    return descriptor


def list_services(directory: Path | None = None) -> tuple[ServiceDescriptor, ...]:
    root = directory or service_config_dir()
    return tuple(load_service(path) for path in sorted(root.glob("*.toml")))


def get_service(service_id: str, directory: Path | None = None) -> ServiceDescriptor:
    services = {service.service_id: service for service in list_services(directory)}
    try:
        return services[service_id]
    except KeyError as error:
        raise KeyError(f"unknown service: {service_id}") from error
