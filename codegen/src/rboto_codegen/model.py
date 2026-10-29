from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Pagination:
    input_tokens: tuple[str, ...]
    output_tokens: tuple[str, ...]
    page_size: str | None = None
    more_results: str | None = None


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
    native_client_class: str | None = None
    display_name: str | None = None
    documentation_path: str = "clients/"
    pagination: tuple[tuple[str, Pagination], ...] = ()

    @property
    def public_name(self) -> str:
        return self.display_name or self.service_id

    @property
    def native_class(self) -> str:
        """Keep the native backend and legacy imports stable across public renames."""
        return self.native_client_class or self.client_class
