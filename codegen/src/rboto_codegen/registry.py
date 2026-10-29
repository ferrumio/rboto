from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

from .model import Pagination, ServiceDescriptor


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


def _pagination(data: dict[str, object]) -> tuple[tuple[str, Pagination], ...]:
    raw = data.get("pagination", {})
    if not isinstance(raw, dict):
        raise ValueError("pagination must be a table")
    result: list[tuple[str, Pagination]] = []
    for operation, value in cast(dict[str, object], raw).items():
        if not isinstance(value, dict):
            raise ValueError(f"pagination.{operation} must be a table")
        entry = cast(dict[str, object], value)
        unknown = entry.keys() - {"input_tokens", "output_tokens", "page_size", "more_results"}
        if unknown:
            raise ValueError(f"unknown pagination fields for {operation}: {sorted(unknown)}")

        def tokens(name: str) -> tuple[str, ...]:
            items = entry.get(name)
            if not isinstance(items, list) or not items:
                raise ValueError(f"pagination.{operation}.{name} must be a nonempty list")
            values = cast(list[object], items)
            if any(not isinstance(item, str) or not item for item in values):
                raise ValueError(f"pagination.{operation}.{name} must contain strings")
            return tuple(cast(list[str], values))

        inputs, outputs = tokens("input_tokens"), tokens("output_tokens")
        if len(inputs) != len(outputs) or len(set(inputs)) != len(inputs):
            raise ValueError(f"pagination.{operation} must pair distinct input tokens with outputs")
        result.append((operation, Pagination(
            inputs, outputs, _optional_string(entry, "page_size"),
            _optional_string(entry, "more_results"),
        )))
    return tuple(result)


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
        force_path_style_for_custom_endpoint=_boolean(data, "force_path_style_for_custom_endpoint"),
        native_outputs=_boolean(data, "native_outputs"),
        native_client_class=_optional_string(data, "native_client_class"),
        display_name=_optional_string(data, "display_name"),
        documentation_path=_optional_string(data, "documentation_path") or "clients/",
        pagination=_pagination(data),
    )
    if path.stem != descriptor.service_id:
        raise ValueError(
            f"service configuration filename {path.stem!r} does not match "
            f"service_id {descriptor.service_id!r}"
        )
    return descriptor


def list_services(directory: Path | None = None) -> tuple[ServiceDescriptor, ...]:
    root = directory or service_config_dir()
    return tuple(
        sorted(
            (load_service(path) for path in root.glob("*.toml")),
            key=lambda service: service.service_id,
        )
    )


def get_service(service_id: str, directory: Path | None = None) -> ServiceDescriptor:
    services = {service.service_id: service for service in list_services(directory)}
    try:
        return services[service_id]
    except KeyError as error:
        raise KeyError(f"unknown service: {service_id}") from error
