from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServiceDescriptor:
    service_id: str
    python_name: str
    distribution_name: str
    python_package: str
    client_class: str
    adapter_crate: str
    rust_crate: str
    rust_module: str
    rust_crate_version: str
    model_url: str
    model_sha256: str
    aws_model_hash: str
    smithy_codegen_revision: str
    customization: str | None = None
    force_path_style_for_custom_endpoint: bool = False
    native_outputs: bool = False
