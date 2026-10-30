from __future__ import annotations

import keyword
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .documentation import render_service_readme
from .manifests import render_cargo, render_pyproject, template_environment
from .model import ServiceDescriptor
from .pagination import resolve_pagination
from .registry import list_services
from .rust_crate import RustField, RustOperation, RustType, parse_crate
from .smithy import SmithyMember, SmithyShape, load_model, short_name, to_snake_case

STRING = "::std::string::String"
DATETIME = "::aws_smithy_types::DateTime"
BLOB = "::aws_smithy_types::Blob"
BYTE_STREAM = "::aws_smithy_types::byte_stream::ByteStream"
DOCUMENT = "::aws_smithy_types::Document"
EVENT_SENDER = "::aws_smithy_http::event_stream::EventStreamSender"


class GenerationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GeneratedPaths:
    facade: Path
    core_init: Path
    core_stub: Path
    cargo: Path
    pyproject: Path
    readme: Path
    license: Path
    package_init: Path
    py_typed: Path
    rust_runtime: Path
    rust: Path
    client: Path
    types: Path
    native_stub: Path
    exceptions: Path
    paginators: Path


def _split_generic(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        elif character == "," and depth == 0:
            part = value[start:index].strip()
            if part:
                parts.append(part)
            start = index + 1
    final = value[start:].strip()
    if final:
        parts.append(final)
    return tuple(parts)


def _generic(rust_type: str, prefix: str) -> tuple[str, ...] | None:
    marker = prefix + "<"
    if rust_type.startswith(marker) and rust_type.endswith(">"):
        return _split_generic(rust_type[len(marker) : -1])
    return None


def _crate_type_name(rust_type: str) -> str | None:
    match = re.fullmatch(r"crate::types::(\w+)", rust_type)
    return match.group(1) if match else None


def _safe_parameter(name: str) -> str:
    return name + "_" if keyword.iskeyword(name) else name


def _write_generated(path: Path, content: str) -> None:
    # Avoid invalidating native builds when shared or service output is unchanged.
    if path.suffix == ".rs":
        # Long SDK paths can require a second formatting pass to stabilize.
        for _ in range(4):
            formatted = subprocess.run(
                ["rustfmt", "--edition", "2024", "--config", "skip_children=true", "--emit", "stdout"],
                input=content,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            if formatted == content:
                break
            content = formatted
        else:
            raise GenerationError(f"rustfmt did not stabilize for {path}")
    if not path.exists() or path.read_text() != content:
        path.write_text(content)


class ServiceGenerator:
    def __init__(
        self,
        descriptor: ServiceDescriptor,
        model_path: Path,
        repository_root: Path,
    ) -> None:
        self.descriptor = descriptor
        self.service = load_model(model_path, descriptor)
        self.crate = parse_crate(descriptor)
        self.repository_root = repository_root
        self.rust_module = descriptor.rust_module
        self._counter = 0
        self._shapes_by_name = {shape.name: shape for shape in self.service.shapes.values()}
        self._shapes_by_rust_name = {
            to_snake_case(shape.name): shape for shape in self.service.shapes.values()
        }
        self._rust_names = {name.lower(): name for name in self.crate.types}
        self.templates = template_environment()
        self._configure_python_types()
        self.pagination = resolve_pagination(
            descriptor,
            self.service,
            {item["operation"] for item in self._native_output_operations()},
            lambda shape, member: (
                field.name if (field := self._member_field(shape, member)) else member.rust_name
            ),
        )

    def _reachable_shapes(self, roots: list[str | None]) -> set[str]:
        pending = list(roots)
        visited: set[str] = set()
        while pending:
            target = pending.pop()
            if target is None or target in visited or target not in self.service.shapes:
                continue
            shape = self.service.shapes[target]
            if shape.error:
                continue
            visited.add(target)
            pending.extend(member.target for member in shape.members)
            pending.extend([shape.member_target, shape.key_target, shape.value_target])
        return visited

    def _configure_python_types(self) -> None:
        self._sequence_type = "_Sequence" if "Sequence" in self._shapes_by_name else "Sequence"
        self._input_targets = self._reachable_shapes(
            [operation.input_target for operation in self.service.operations]
        )
        outputs = self._reachable_shapes(
            [operation.output_target for operation in self.service.operations]
        )
        native = self._native_output_operations()
        native_names = {item["operation"] for item in native}
        dictionary_roots: list[str | None] = [
            target for target in outputs if self.service.shapes[target].kind == "union"
        ]
        dictionary_roots.extend(
            operation.output_target
            for operation in self.service.operations
            if operation.rust_name not in native_names
        )
        self._dictionary_output_targets = self._reachable_shapes(dictionary_roots)
        reserved = {shape.name for shape in self.service.shapes.values()}
        reserved.update(item["class_name"] for item in native)
        reserved.update(
            f"{shape.name}{member.name}"
            for shape in self.service.shapes.values()
            if shape.kind == "union"
            for member in shape.members
        )
        self._output_type_names: dict[str, str] = {}
        for shape in sorted(self.service.shapes.values(), key=lambda shape: shape.name):
            if not shape.enum_values and (
                shape.kind not in {"structure", "union"}
                or shape.shape_id not in self._dictionary_output_targets
            ):
                continue
            name = shape.name
            if shape.enum_values or shape.shape_id in self._input_targets:
                name += "Response" if name.endswith("Output") else "Output"
                while name in reserved:
                    name += "Value"
            reserved.add(name)
            self._output_type_names[shape.shape_id] = name

    def _output_type_name(self, shape: SmithyShape) -> str:
        return self._output_type_names[shape.shape_id]

    def _shape_fields(self, shape: SmithyShape) -> tuple[RustField, ...]:
        name = self._rust_names.get(shape.name.lower(), "")
        if name in self.crate.types:
            return self.crate.types[name].fields
        for operation in self.service.operations:
            if operation.output_target == shape.shape_id:
                return self.crate.operations[operation.rust_name].output_fields
        return ()

    def _member_field(self, shape: SmithyShape, member: SmithyMember) -> RustField | None:
        return next(
            (
                field for field in self._shape_fields(shape)
                if field.name.replace("_", "") == member.rust_name.replace("_", "")
            ),
            None,
        )

    def generate(self) -> GeneratedPaths:
        paths = GeneratedPaths(
            facade=self.repository_root / "packages/rboto/src/rboto/services.py",
            core_init=self.repository_root / "packages/rboto/src/rboto/__init__.py",
            core_stub=self.repository_root / "packages/rboto/src/rboto/__init__.pyi",
            cargo=self.repository_root / f"crates/{self.descriptor.distribution_name}/Cargo.toml",
            pyproject=self.repository_root
            / f"packages/{self.descriptor.distribution_name}/pyproject.toml",
            readme=self.repository_root
            / f"packages/{self.descriptor.distribution_name}/README.md",
            license=self.repository_root
            / f"packages/{self.descriptor.distribution_name}/LICENSE",
            package_init=self.repository_root
            / f"packages/{self.descriptor.distribution_name}/python/"
            f"{self.descriptor.python_package}/__init__.py",
            py_typed=self.repository_root / f"packages/{self.descriptor.distribution_name}/python/"
            f"{self.descriptor.python_package}/py.typed",
            rust_runtime=self.repository_root
            / f"crates/{self.descriptor.distribution_name}/src/lib.rs",
            rust=self.repository_root
            / f"crates/{self.descriptor.distribution_name}/src/generated.rs",
            client=self.repository_root / f"packages/{self.descriptor.distribution_name}/python/"
            f"{self.descriptor.python_package}/client.py",
            types=self.repository_root / f"packages/{self.descriptor.distribution_name}/python/"
            f"{self.descriptor.python_package}/types.py",
            native_stub=self.repository_root
            / f"packages/{self.descriptor.distribution_name}/python/"
            f"{self.descriptor.python_package}/_native.pyi",
            exceptions=self.repository_root
            / f"packages/{self.descriptor.distribution_name}/python/"
            f"{self.descriptor.python_package}/exceptions.py",
            paginators=self.repository_root / f"packages/{self.descriptor.distribution_name}/python/"
            f"{self.descriptor.python_package}/paginators.py",
        )
        for path in (
            paths.rust,
            paths.facade,
            paths.core_init,
            paths.core_stub,
            paths.cargo,
            paths.pyproject,
            paths.readme,
            paths.license,
            paths.package_init,
            paths.py_typed,
            paths.rust_runtime,
            paths.client,
            paths.types,
            paths.native_stub,
            paths.exceptions,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)

        _write_generated(paths.facade, self._render_facade())
        _write_generated(paths.core_init, self._render_core_init())
        _write_generated(
            paths.core_stub,
            self.templates.get_template("core_init.pyi.j2").render(services=list_services()),
        )
        _write_generated(paths.cargo, self._render_cargo())
        _write_generated(paths.pyproject, self._render_pyproject())
        _write_generated(paths.license, (self.repository_root / "LICENSE").read_text())
        _write_generated(paths.package_init, self._render_package_init())
        _write_generated(paths.py_typed, "")
        _write_generated(paths.rust_runtime, self._render_rust_runtime())
        _write_generated(paths.rust, self._render_rust())
        _write_generated(paths.types, self._render_python_types())
        client_source = self._render_python_client()
        _write_generated(paths.client, client_source)
        if self.pagination:
            _write_generated(paths.paginators, self._render_python_client(paginators=True))
        elif paths.paginators.exists():
            paths.paginators.unlink()
        _write_generated(
            paths.readme,
            render_service_readme(
                self.descriptor, client_source, self.repository_root,
                tuple(item.operation for item in self.pagination),
            ),
        )
        _write_generated(paths.native_stub, self._render_native_stub())
        _write_generated(paths.exceptions, self._render_exceptions())
        return paths

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"

    def _rust_type(self, rust_type: str) -> RustType | None:
        name = _crate_type_name(rust_type)
        return self.crate.types.get(name) if name else None

    def _shape_for_rust(self, name: str) -> SmithyShape | None:
        return self._shapes_by_name.get(name) or self._shapes_by_rust_name.get(to_snake_case(name))

    def _public_rust_type(self, rust_type: str) -> str:
        return (
            rust_type.replace(
                EVENT_SENDER,
                f"{self.rust_module}::primitives::event_stream::EventStreamSender",
            )
            .replace(
                "crate::event_receiver::EventReceiver",
                f"{self.rust_module}::primitives::event_stream::EventReceiver",
            )
            .replace("crate::", f"{self.rust_module}::")
        )

    def _event_streams(self) -> list[dict[str, str]]:
        streams: dict[str, dict[str, str]] = {}
        for operation in self.crate.operations.values():
            for field in operation.output_fields:
                if self._kind(field.rust_type) != "event_receiver":
                    continue
                arguments = _generic(field.rust_type, "crate::event_receiver::EventReceiver")
                if arguments is None or len(arguments) != 2:
                    raise GenerationError(f"invalid event receiver: {field.rust_type}")
                event_name = _crate_type_name(arguments[0])
                if event_name is None:
                    raise GenerationError(f"invalid event type: {arguments[0]}")
                error_type = self._public_rust_type(arguments[1])
                error_name = arguments[1].rsplit("::", 1)[-1]
                error_source = (self.crate.path / "src/types/error.rs").read_text()
                error_enum = re.search(
                    rf"pub enum {re.escape(error_name)}\s*\{{(.*?)\n\}}",
                    error_source,
                    re.DOTALL,
                )
                if error_enum is None:
                    raise GenerationError(f"missing event error enum: {error_name}")
                error_arms: list[str] = []
                for variant, error_shape_name in re.findall(
                    r"^\s*(\w+)\(crate::types::error::(\w+)\),",
                    error_enum.group(1),
                    re.MULTILINE,
                ):
                    shape = self._shape_for_rust(error_shape_name)
                    if shape is None or not shape.error:
                        raise GenerationError(f"missing modeled event error: {error_shape_name}")
                    error_arms.append(
                        f'                        Some({error_type}::{variant}(_)) => Some("{shape.name}"),'
                    )
                streams[event_name] = {
                    "event_name": event_name,
                    "receiver_class": f"{event_name}Receiver",
                    "rust_class": f"Py{event_name}Receiver",
                    "rust_type": self._public_rust_type(field.rust_type),
                    "converter": f"{to_snake_case(event_name)}_to_py",
                    "error_arms": "\n".join(error_arms),
                }
        return [streams[name] for name in sorted(streams)]

    def _kind(self, rust_type: str) -> str:
        if rust_type == STRING:
            return "string"
        if rust_type in {"bool", "i8", "i16", "i32", "i64", "f32", "f64"}:
            return rust_type
        if rust_type == DOCUMENT:
            return "document"
        if _generic(rust_type, "::std::boxed::Box") is not None:
            return "box"
        if _generic(rust_type, "::std::option::Option") is not None:
            return "option"
        if _generic(rust_type, EVENT_SENDER) is not None:
            return "event_sender"
        if rust_type == DATETIME:
            return "datetime"
        if rust_type == BLOB:
            return "blob"
        if rust_type == BYTE_STREAM:
            return "byte_stream"
        if _generic(rust_type, "::std::vec::Vec") is not None:
            return "vec"
        if _generic(rust_type, "::std::collections::HashMap") is not None:
            return "map"
        if rust_type.startswith("crate::event_receiver::EventReceiver<"):
            return "event_receiver"
        rust = self._rust_type(rust_type)
        if rust is not None:
            shape = self._shape_for_rust(rust.name)
            if rust.union or (shape is not None and shape.kind == "union"):
                return "union"
            if rust.is_struct:
                return "struct"
            return "enum"
        raise GenerationError(f"unsupported Rust type: {rust_type}")

    def _from_py(self, rust_type: str, source: str, target: str, indent: str) -> list[str]:
        kind = self._kind(rust_type)
        lines: list[str] = []
        if kind == "string":
            return [f"{indent}let {target}: String = {source}.extract()?;"]
        if kind in {"bool", "i8", "i16", "i32", "i64", "f32", "f64"}:
            return [f"{indent}let {target}: {kind} = {source}.extract()?;"]
        if kind == "document":
            return [f"{indent}let {target} = rboto_core::document_from_py(&{source})?;"]
        if kind in {"box", "option"}:
            prefix = "::std::boxed::Box" if kind == "box" else "::std::option::Option"
            arguments = _generic(rust_type, prefix)
            assert arguments is not None
            inner = self._next("inner")
            if kind == "box":
                return [
                    *self._from_py(arguments[0], source, inner, indent),
                    f"{indent}let {target} = Box::new({inner});",
                ]
            return [
                f"{indent}let {target} = if {source}.is_none() {{ None }} else {{",
                *self._from_py(arguments[0], source, inner, indent + "    "),
                f"{indent}    Some({inner})",
                f"{indent}}};",
            ]
        if kind == "event_sender":
            arguments = _generic(rust_type, EVENT_SENDER)
            assert arguments is not None and len(arguments) == 2
            event = _crate_type_name(arguments[0])
            error = self._public_rust_type(arguments[1])
            sender = self._public_rust_type(rust_type)
            return [
                f"{indent}let {target}: {sender} = rboto_core::python_event_stream(",
                f"{indent}    &{source}, {to_snake_case(event or '')}_from_py,",
                f"{indent}    {error}::unhandled,",
                f"{indent})?.into();",
            ]
        if kind == "datetime":
            raw = self._next("timestamp")
            return [
                f"{indent}let {raw}: String = {source}.extract()?;",
                f"{indent}let {target} = ::aws_smithy_types::DateTime::from_str(",
                f"{indent}    &{raw}, ::aws_smithy_types::date_time::Format::DateTime",
                f"{indent}).map_err(|error| PyValueError::new_err(error.to_string()))?;",
            ]
        if kind == "blob":
            raw = self._next("bytes")
            return [
                f"{indent}let {raw}: Vec<u8> = {source}.extract()?;",
                f"{indent}let {target} = ::aws_smithy_types::Blob::new({raw});",
            ]
        if kind == "byte_stream":
            raw = self._next("bytes")
            return [
                f"{indent}let {raw}: Vec<u8> = {source}.extract()?;",
                f"{indent}let {target} = AwsByteStream::from({raw});",
            ]
        if kind == "enum":
            name = _crate_type_name(rust_type)
            raw = self._next("enum_value")
            return [
                f"{indent}let {raw}: String = {source}.extract()?;",
                f"{indent}let {target} = {self.rust_module}::types::{name}::from({raw}.as_str());",
            ]
        if kind in {"struct", "union"}:
            name = _crate_type_name(rust_type)
            return [f"{indent}let {target} = {to_snake_case(name or '')}_from_py(&{source})?;"]
        if kind == "vec":
            arguments = _generic(rust_type, "::std::vec::Vec")
            if arguments is None or len(arguments) != 1:
                raise GenerationError(f"invalid Vec type: {rust_type}")
            item_type = arguments[0]
            iterator = self._next("item_result")
            item = self._next("item")
            converted = self._next("converted_item")
            # Reserve only for concrete lists. Other accepted iterables may not
            # have a length, or may execute user code when a length is requested.
            lines.append(
                f"{indent}let mut {target} = Vec::with_capacity("
                f"{source}.cast::<PyList>().map_or(0, |items| items.len()));"
            )
            lines.append(f"{indent}for {iterator} in {source}.try_iter()? {{")
            lines.append(f"{indent}    let {item} = {iterator}?;")
            lines.extend(self._from_py(item_type, item, converted, indent + "    "))
            lines.append(f"{indent}    {target}.push({converted});")
            lines.append(f"{indent}}}")
            return lines
        if kind == "map":
            arguments = _generic(rust_type, "::std::collections::HashMap")
            if arguments is None or len(arguments) != 2:
                raise GenerationError(f"invalid HashMap type: {rust_type}")
            key_type, value_type = arguments
            dictionary = self._next("mapping")
            key = self._next("key")
            value = self._next("value")
            converted_key = self._next("converted_key")
            converted_value = self._next("converted_value")
            lines.append(f"{indent}let {dictionary} = {source}.cast::<PyDict>()?;")
            lines.append(
                f"{indent}let mut {target} = HashMap::with_capacity({dictionary}.len());"
            )
            lines.append(f"{indent}for ({key}, {value}) in {dictionary}.iter() {{")
            lines.extend(self._from_py(key_type, key, converted_key, indent + "    "))
            lines.extend(self._from_py(value_type, value, converted_value, indent + "    "))
            lines.append(f"{indent}    {target}.insert({converted_key}, {converted_value});")
            lines.append(f"{indent}}}")
            return lines
        raise GenerationError(f"cannot convert Python input to {rust_type}")

    def _to_py(self, rust_type: str, source: str, target: str, indent: str) -> list[str]:
        kind = self._kind(rust_type)
        if kind == "string":
            return [f"{indent}let {target} = ({source}).as_str().into_py_any(py)?;"]
        if kind in {"bool", "i8", "i16", "i32", "i64", "f32", "f64"}:
            return [f"{indent}let {target} = ({source}).to_owned().into_py_any(py)?;"]
        if kind == "document":
            return [f"{indent}let {target} = rboto_core::document_to_py(py, {source})?;"]
        if kind == "box":
            arguments = _generic(rust_type, "::std::boxed::Box")
            assert arguments is not None
            return self._to_py(arguments[0], f"({source}).as_ref()", target, indent)
        if kind == "option":
            arguments = _generic(rust_type, "::std::option::Option")
            assert arguments is not None
            inner = self._next("inner")
            return [
                f"{indent}let {target} = if let Some(value) = {source} {{",
                *self._to_py(arguments[0], "value", inner, indent + "    "),
                f"{indent}    {inner}",
                f"{indent}}} else {{ py.None() }};",
            ]
        if kind == "datetime":
            return [f"{indent}let {target} = ({source}).to_string().into_py_any(py)?;"]
        if kind == "blob":
            return [
                f"{indent}let {target} = PyBytes::new(py, ({source}).as_ref()).into_any().unbind();"
            ]
        if kind == "enum":
            return [f"{indent}let {target} = ({source}).as_str().into_py_any(py)?;"]
        if kind in {"struct", "union"}:
            name = _crate_type_name(rust_type)
            return [f"{indent}let {target} = {to_snake_case(name or '')}_to_py(py, {source})?;"]
        if kind == "vec":
            arguments = _generic(rust_type, "::std::vec::Vec")
            if arguments is None or len(arguments) != 1:
                raise GenerationError(f"invalid Vec type: {rust_type}")
            item = self._next("item")
            converted = self._next("converted_item")
            result = [f"{indent}let {target}_list = PyList::empty(py);"]
            result.append(f"{indent}for {item} in {source} {{")
            result.extend(self._to_py(arguments[0], item, converted, indent + "    "))
            result.append(f"{indent}    {target}_list.append({converted})?;")
            result.append(f"{indent}}}")
            result.append(f"{indent}let {target} = {target}_list.into_any().unbind();")
            return result
        if kind == "map":
            arguments = _generic(rust_type, "::std::collections::HashMap")
            if arguments is None or len(arguments) != 2:
                raise GenerationError(f"invalid HashMap type: {rust_type}")
            key = self._next("key")
            value = self._next("value")
            converted_key = self._next("converted_key")
            converted_value = self._next("converted_value")
            result = [f"{indent}let {target}_dict = PyDict::new(py);"]
            result.append(f"{indent}for ({key}, {value}) in {source} {{")
            result.extend(self._to_py(arguments[0], key, converted_key, indent + "    "))
            result.extend(self._to_py(arguments[1], value, converted_value, indent + "    "))
            result.append(
                f"{indent}    {target}_dict.set_item({converted_key}, {converted_value})?;"
            )
            result.append(f"{indent}}}")
            result.append(f"{indent}let {target} = {target}_dict.into_any().unbind();")
            return result
        raise GenerationError(f"cannot convert Rust output {rust_type} to Python")

    def _to_typed_py(self, rust_type: str, source: str, target: str, indent: str) -> list[str]:
        kind = self._kind(rust_type)
        if kind == "box":
            arguments = _generic(rust_type, "::std::boxed::Box")
            assert arguments is not None
            return self._to_typed_py(arguments[0], f"({source}).as_ref()", target, indent)
        if kind == "option":
            arguments = _generic(rust_type, "::std::option::Option")
            assert arguments is not None
            inner = self._next("inner")
            return [
                f"{indent}let {target} = if let Some(value) = {source} {{",
                *self._to_typed_py(arguments[0], "value", inner, indent + "    "),
                f"{indent}    {inner}",
                f"{indent}}} else {{ py.None() }};",
            ]
        if kind == "struct":
            name = _crate_type_name(rust_type)
            return [
                f"{indent}let {target} = Py::new(py, Py{name} {{ inner: ({source}).to_owned() }})?.into_any();"
            ]
        if kind == "vec":
            arguments = _generic(rust_type, "::std::vec::Vec")
            if arguments is None or len(arguments) != 1:
                raise GenerationError(f"invalid Vec type: {rust_type}")
            item = self._next("item")
            converted = self._next("converted_item")
            lines = [f"{indent}let {target}_list = PyList::empty(py);"]
            lines.append(f"{indent}for {item} in {source} {{")
            lines.extend(self._to_typed_py(arguments[0], item, converted, indent + "    "))
            lines.append(f"{indent}    {target}_list.append({converted})?;")
            lines.append(f"{indent}}}")
            lines.append(f"{indent}let {target} = {target}_list.into_any().unbind();")
            return lines
        if kind == "map":
            arguments = _generic(rust_type, "::std::collections::HashMap")
            if arguments is None or len(arguments) != 2:
                raise GenerationError(f"invalid HashMap type: {rust_type}")
            key = self._next("key")
            value = self._next("value")
            converted_key = self._next("converted_key")
            converted_value = self._next("converted_value")
            lines = [f"{indent}let {target}_dict = PyDict::new(py);"]
            lines.append(f"{indent}for ({key}, {value}) in {source} {{")
            lines.extend(self._to_py(arguments[0], key, converted_key, indent + "    "))
            lines.extend(self._to_typed_py(arguments[1], value, converted_value, indent + "    "))
            lines.append(
                f"{indent}    {target}_dict.set_item({converted_key}, {converted_value})?;"
            )
            lines.append(f"{indent}}}")
            lines.append(f"{indent}let {target} = {target}_dict.into_any().unbind();")
            return lines
        return self._to_py(rust_type, source, target, indent)

    def _native_structs(self) -> tuple[RustType, ...]:
        if not self.descriptor.native_outputs:
            return ()
        names: set[str] = set()

        def visit(rust_type: str) -> None:
            kind = self._kind(rust_type)
            if kind == "struct":
                name = _crate_type_name(rust_type)
                if name is None or name in names:
                    return
                names.add(name)
                rust = self.crate.types[name]
                for field in rust.fields:
                    visit(field.rust_type)
            elif kind == "vec":
                arguments = _generic(rust_type, "::std::vec::Vec")
                if arguments:
                    visit(arguments[0])
            elif kind == "map":
                arguments = _generic(rust_type, "::std::collections::HashMap")
                if arguments and len(arguments) == 2:
                    visit(arguments[1])
            elif kind in {"box", "option"}:
                prefix = "::std::boxed::Box" if kind == "box" else "::std::option::Option"
                arguments = _generic(rust_type, prefix)
                if arguments:
                    visit(arguments[0])

        for operation in self.crate.operations.values():
            for field in operation.output_fields:
                if self._kind(field.rust_type) not in {"byte_stream", "event_receiver"}:
                    visit(field.rust_type)
        return tuple(self.crate.types[name] for name in sorted(names))

    def _native_output_operations(self) -> list[dict[str, str]]:
        if not self.descriptor.native_outputs:
            return []
        target_counts: dict[str, int] = {}
        input_member_targets: set[str] = set()
        for operation in self.service.operations:
            if operation.output_target not in {None, "smithy.api#Unit"}:
                target = operation.output_target
                assert target is not None
                target_counts[target] = target_counts.get(target, 0) + 1
            if operation.input_target not in {None, "smithy.api#Unit"}:
                assert operation.input_target is not None
                input_member_targets.update(
                    member.target for member in self.service.shapes[operation.input_target].members
                )
        outputs: list[dict[str, str]] = []
        for smithy in self.service.operations:
            if smithy.output_target is None or smithy.output_target == "smithy.api#Unit":
                continue
            rust_operation = self.crate.operations[smithy.rust_name]
            if rust_operation.output_type is None:
                raise GenerationError(f"missing Rust output type for operation {smithy.rust_name}")
            if any(
                self._kind(field.rust_type) == "event_receiver"
                for field in rust_operation.output_fields
            ):
                continue
            class_name = short_name(smithy.output_target)
            if (
                target_counts[smithy.output_target] > 1
                or smithy.output_target in input_member_targets
                or class_name in self.crate.types
            ):
                class_name = f"{smithy.name}Output"
            outputs.append(
                {
                    "operation": smithy.rust_name,
                    "class_name": class_name,
                    "rust_class": f"Py{class_name}",
                    "rust_type": (
                        f"{self.rust_module}::operation::{smithy.rust_name}::"
                        f"{rust_operation.output_type}"
                    ),
                }
            )
        return outputs

    def _native_getter(self, field: RustField, source: str = "self.inner") -> str:
        property_name = _safe_parameter(field.name)
        if property_name == field.name:
            property_name = field.rust_name
        # PyO3 otherwise strips a leading get_ from getter method names.
        getter = (
            f"    #[getter({property_name})]"
            if field.name.startswith("get_")
            else "    #[getter]"
        )
        if self._kind(field.rust_type) == "byte_stream":
            expression = (
                f"self.{field.name}.as_ref().map(|stream| stream.clone_ref(py))"
                if field.optional
                else f"self.{field.name}.clone_ref(py)"
            )
            return "\n".join(
                [
                    getter,
                    f"    fn {property_name}(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {{",
                    f"        {expression}.into_py_any(py)",
                    "    }",
                ]
            )
        converted = self._next("converted")
        lines = [
            getter,
            f"    fn {property_name}(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {{",
        ]
        if property_name.startswith("from_"):
            # This is a modeled read-only property, not a Rust constructor.
            lines.insert(0, "    #[allow(clippy::wrong_self_convention)]")
        if field.optional:
            lines.append(f"        if let Some(value) = &{source}.{field.rust_name} {{")
            lines.extend(self._to_typed_py(field.rust_type, "value", converted, "            "))
            lines.append(f"            Ok({converted})")
            lines.append("        } else {")
            lines.append("            Ok(py.None())")
            lines.append("        }")
        else:
            lines.extend(
                self._to_typed_py(
                    field.rust_type,
                    f"&{source}.{field.rust_name}",
                    converted,
                    "        ",
                )
            )
            lines.append(f"        Ok({converted})")
        lines.append("    }")
        return "\n".join(lines)

    def _native_struct_class(self, rust: RustType) -> str:
        getters = "\n\n".join(self._native_getter(field) for field in rust.fields)
        converter = f"{to_snake_case(rust.name)}_to_py"
        return "\n".join(
            [
                f'#[pyclass(name = "{rust.name}", module = "{self.descriptor.python_package}._native", frozen)]',
                f"struct Py{rust.name} {{",
                f"    inner: {self.rust_module}::types::{rust.name},",
                "}",
                "",
                "#[pymethods]",
                f"impl Py{rust.name} {{",
                getters,
                "",
                "    fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {",
                f"        {converter}(py, &self.inner)",
                "    }",
                "}",
            ]
        )

    def _operation_dict_lines(
        self,
        operation: RustOperation,
        source: str,
        indent: str,
        stream_owner: str | None = None,
    ) -> list[str]:
        lines = [f"{indent}let result = PyDict::new(py);"]
        for field in operation.output_fields:
            if self._kind(field.rust_type) == "byte_stream":
                if stream_owner is None:
                    raise GenerationError("ByteStream output requires a stream owner")
                lines.append(
                    f'{indent}result.set_item("{field.name}", '
                    + (
                        f"{stream_owner}.{field.name}.as_ref().map(|stream| stream.clone_ref(py)))?;"
                        if field.optional
                        else f"{stream_owner}.{field.name}.clone_ref(py))?;"
                    )
                )
                continue
            converted = self._next("converted")
            if field.optional:
                lines.append(f"{indent}if let Some(value) = &{source}.{field.rust_name} {{")
                lines.extend(self._to_py(field.rust_type, "value", converted, indent + "    "))
                lines.append(f'{indent}    result.set_item("{field.name}", {converted})?;')
                lines.append(f"{indent}}} else {{")
                lines.append(f'{indent}    result.set_item("{field.name}", py.None())?;')
                lines.append(f"{indent}}}")
            else:
                lines.extend(
                    self._to_py(
                        field.rust_type,
                        f"&{source}.{field.rust_name}",
                        converted,
                        indent,
                    )
                )
                lines.append(f'{indent}result.set_item("{field.name}", {converted})?;')
        lines.append(f"{indent}Ok(result.into_any().unbind())")
        return lines

    def _native_operation_class(self, metadata: dict[str, str]) -> str:
        operation = self.crate.operations[metadata["operation"]]
        getters = "\n\n".join(self._native_getter(field) for field in operation.output_fields)
        stream_fields = [
            field
            for field in operation.output_fields
            if self._kind(field.rust_type) == "byte_stream"
        ]
        lines = [
            f'#[pyclass(name = "{metadata["class_name"]}", module = "{self.descriptor.python_package}._native", frozen)]',
            f"struct {metadata['rust_class']} {{",
            f"    inner: {metadata['rust_type']},",
            *(
                f"    {field.name}: "
                + ("Option<Py<PyByteStream>>," if field.optional else "Py<PyByteStream>,")
                for field in stream_fields
            ),
            "}",
            "",
            "#[pymethods]",
            f"impl {metadata['rust_class']} {{",
            getters,
            "",
            "    fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {",
        ]
        lines.extend(
            self._operation_dict_lines(operation, "self.inner", "        ", stream_owner="self")
        )
        lines.extend(["    }", "}"])
        return "\n".join(lines)

    def _struct_from_py(self, rust: RustType) -> str:
        name = to_snake_case(rust.name)
        lines = [
            f"fn {name}_from_py(value: &Bound<'_, PyAny>) -> PyResult<{self.rust_module}::types::{rust.name}> {{",
            "    let values = value.cast::<PyDict>()?;",
            f"    let mut builder = {self.rust_module}::types::{rust.name}::builder();",
        ]
        for field in rust.fields:
            converted = self._next("converted")
            lines.append(f'    if let Some(value) = dict_value(values, "{field.name}")? {{')
            lines.extend(self._from_py(field.rust_type, "value", converted, "        "))
            lines.append(f"        builder = builder.set_{field.name}(Some({converted}));")
            lines.append("    }")
        if rust.build_fallible:
            lines.append(
                "    builder.build().map_err(|error| PyValueError::new_err(error.to_string()))"
            )
        else:
            lines.append("    Ok(builder.build())")
        lines.append("}")
        return "\n".join(lines)

    def _struct_to_py(self, rust: RustType) -> str:
        name = to_snake_case(rust.name)
        lines = [
            f"fn {name}_to_py(py: Python<'_>, value: &{self.rust_module}::types::{rust.name}) -> PyResult<Py<PyAny>> {{",
            "    let result = PyDict::new(py);",
        ]
        for field in rust.fields:
            converted = self._next("converted")
            if field.optional:
                lines.append(f"    if let Some(value) = &value.{field.rust_name} {{")
                lines.extend(self._to_py(field.rust_type, "value", converted, "        "))
                lines.append(f'        result.set_item("{field.name}", {converted})?;')
                lines.append("    } else {")
                lines.append(f'        result.set_item("{field.name}", py.None())?;')
                lines.append("    }")
            else:
                lines.extend(
                    self._to_py(
                        field.rust_type,
                        f"&value.{field.rust_name}",
                        converted,
                        "    ",
                    )
                )
                lines.append(f'    result.set_item("{field.name}", {converted})?;')
        lines.extend(["    Ok(result.into_any().unbind())", "}"])
        return "\n".join(lines)

    def _model_rust_type(self, target: str) -> str:
        builtin = {
            "smithy.api#String": STRING,
            "smithy.api#Boolean": "bool",
            "smithy.api#Integer": "i32",
            "smithy.api#Long": "i64",
            "smithy.api#Float": "f64",
            "smithy.api#Double": "f64",
            "smithy.api#Blob": BLOB,
            "smithy.api#Timestamp": DATETIME,
            "smithy.api#Document": DOCUMENT,
        }
        if target in builtin:
            return builtin[target]
        shape = self.service.shapes[target]
        if shape.kind in {"string", "enum"}:
            name = self._rust_names.get(shape.name.lower(), shape.name)
            return f"crate::types::{name}" if shape.enum_values else STRING
        if shape.kind in {"structure", "union"}:
            return f"crate::types::{self._rust_names.get(shape.name.lower(), shape.name)}"
        if shape.kind == "document":
            return DOCUMENT
        if shape.kind in {"list", "set"} and shape.member_target is not None:
            return f"::std::vec::Vec<{self._model_rust_type(shape.member_target)}>"
        if shape.kind == "map" and shape.key_target and shape.value_target:
            return (
                "::std::collections::HashMap<"
                f"{self._model_rust_type(shape.key_target)}, "
                f"{self._model_rust_type(shape.value_target)}>"
            )
        if shape.kind == "blob":
            return BYTE_STREAM if shape.streaming else BLOB
        if shape.kind == "timestamp":
            return DATETIME
        if shape.kind == "boolean":
            return "bool"
        if shape.kind in {"byte", "short", "integer"}:
            return "i32"
        if shape.kind == "long":
            return "i64"
        if shape.kind in {"float", "double", "bigDecimal"}:
            return "f64"
        raise GenerationError(f"cannot map Smithy target to Rust: {target} ({shape.kind})")

    def _union_from_py(self, rust: RustType, shape: SmithyShape) -> str:
        fn_name = to_snake_case(rust.name)
        lines = [
            f"fn {fn_name}_from_py(value: &Bound<'_, PyAny>) -> PyResult<{self.rust_module}::types::{rust.name}> {{",
            "    let values = value.cast::<PyDict>()?;",
            '    if values.len() != 1 { return Err(PyValueError::new_err("union requires exactly one variant")); }',
        ]
        for member in shape.members:
            if (
                self.service.shapes.get(member.target) is not None
                and self.service.shapes[member.target].error
            ):
                # Smithy models stream errors as members; Rust exposes them via recv().
                continue
            variant = next(
                (
                    candidate
                    for candidate in rust.enum_variants
                    if candidate.lower() == member.name.lower()
                ),
                member.name,
            )
            converted = self._next("converted")
            rust_type = dict(rust.variant_types).get(variant)
            if member.target == "smithy.api#Unit" and variant in rust.enum_variants:
                lines.extend(
                    [
                        f'    if let Some(value) = values.get_item("{member.rust_name}")? {{',
                        '        if !value.is_none() { return Err(PyValueError::new_err("unit variant requires None")); }',
                        f"        return Ok({self.rust_module}::types::{rust.name}::{variant});",
                        "    }",
                    ]
                )
                continue
            if rust_type is None:
                raise GenerationError(f"missing Rust union variant: {rust.name}.{variant}")
            lines.append(f'    if let Some(value) = dict_value(values, "{member.rust_name}")? {{')
            lines.extend(self._from_py(rust_type, "value", converted, "        "))
            lines.append(
                f"        return Ok({self.rust_module}::types::{rust.name}::{variant}({converted}));"
            )
            lines.append("    }")
        lines.extend(
            [
                f'    Err(PyValueError::new_err("{rust.name} requires exactly one known variant"))',
                "}",
            ]
        )
        return "\n".join(lines)

    def _union_to_py(self, rust: RustType, shape: SmithyShape) -> str:
        fn_name = to_snake_case(rust.name)
        lines = [
            f"fn {fn_name}_to_py(py: Python<'_>, value: &{self.rust_module}::types::{rust.name}) -> PyResult<Py<PyAny>> {{",
            "    let result = PyDict::new(py);",
            "    match value {",
        ]
        for member in shape.members:
            if (
                self.service.shapes.get(member.target) is not None
                and self.service.shapes[member.target].error
            ):
                continue
            variant = next(
                (
                    candidate
                    for candidate in rust.enum_variants
                    if candidate.lower() == member.name.lower()
                ),
                member.name,
            )
            converted = self._next("converted")
            rust_type = dict(rust.variant_types).get(variant)
            if member.target == "smithy.api#Unit" and variant in rust.enum_variants:
                lines.append(
                    f'        {self.rust_module}::types::{rust.name}::{variant} => {{ result.set_item("{member.rust_name}", py.None())?; }}'
                )
                continue
            if rust_type is None:
                raise GenerationError(f"missing Rust union variant: {rust.name}.{variant}")
            lines.append(f"        {self.rust_module}::types::{rust.name}::{variant}(value) => {{")
            lines.extend(self._to_py(rust_type, "value", converted, "            "))
            lines.append(f'            result.set_item("{member.rust_name}", {converted})?;')
            lines.append("        }")
        lines.extend(
            [
                '        _ => { result.set_item("unknown", true)?; }',
                "    }",
                "    Ok(result.into_any().unbind())",
                "}",
            ]
        )
        return "\n".join(lines)

    def _operation_method(self, operation_name: str) -> str:
        operation = self.crate.operations[operation_name]
        smithy = next(op for op in self.service.operations if op.rust_name == operation_name)
        native_metadata = next(
            (
                metadata
                for metadata in self._native_output_operations()
                if metadata["operation"] == operation_name
            ),
            None,
        )
        has_native_stream = native_metadata is not None and any(
            self._kind(field.rust_type) == "byte_stream" for field in operation.output_fields
        )
        lines = [
            "    #[pyo3(signature = (params, observation=None))]",
            f"    fn {operation_name}<'py>(&self, py: Python<'py>, params: &Bound<'py, PyDict>, observation: Option<Py<PyDict>>) -> PyResult<Bound<'py, PyAny>> {{",
            "        let state = self.state.clone();",
        ]
        converted_fields: list[tuple[RustField, str]] = []
        for field in operation.input_fields:
            converted = f"{field.name}_value"
            inner = self._next("converted")
            lines.append(
                f'        let {converted} = if let Some(value) = dict_value(params, "{field.name}")? {{'
            )
            lines.extend(self._from_py(field.rust_type, "value", inner, "            "))
            lines.append(f"            Some({inner})")
            lines.append("        } else { None };")
            converted_fields.append((field, converted))

        lines.extend(
            [
                "        future_into_py(py, async move {",
                "            let client = state.client().await?;",
                f"            let request = client.{operation_name}()",
            ]
        )
        for field, converted in converted_fields:
            lines.append(f"                .set_{field.name}({converted})")
        lines[-1] += ";"
        lines.extend(
            [
                "            let result = if let Some(sink) = observation {",
                "                let observer = rboto_core::OperationObservation::default();",
                "                let result = request.customize().interceptor(observer.clone()).send().await;",
                "                observer.publish(sink, client.config().region().map(|region| region.as_ref()));",
                "                result",
                "            } else {",
                "                request.send().await",
                "            };",
                f"            let {'mut ' if has_native_stream else ''}output = result.map_err(|error| {{",
                f'                Python::attach(|py| sdk_error_to_py(py, &error, "{smithy.name}"))',
                "            })?;",
            ]
        )

        if native_metadata is not None and not has_native_stream:
            # The async bridge converts this owned value while delivering the
            # result, so creating the Python object here would attach twice.
            lines.extend(
                [
                    f"            Ok({native_metadata['rust_class']} {{ inner: output }})",
                    "        })",
                    "    }",
                ]
            )
            return "\n".join(lines)

        lines.append("            Python::attach(|py| {")

        if smithy.output_target is None or smithy.output_target == "smithy.api#Unit":
            lines.extend(["                Ok(py.None())", "            })", "        })", "    }"])
            return "\n".join(lines)

        if native_metadata is not None:
            output_class = native_metadata["class_name"]
            constructor_fields: list[str] = ["inner: output"]
            stream_setup: list[str] = []
            for field in operation.output_fields:
                if self._kind(field.rust_type) != "byte_stream":
                    continue
                stream_name = f"py_{field.name}"
                constructor = (
                    f"{field.name}.map(|stream| Py::new(py, PyByteStream::new(stream))).transpose()?"
                    if field.optional
                    else f"Py::new(py, PyByteStream::new({field.name}))?"
                )
                stream_setup.extend(
                    [
                        f"                let {field.name} = std::mem::take(&mut output.{field.rust_name});",
                        f"                let {stream_name} = {constructor};",
                    ]
                )
                constructor_fields.append(f"{field.name}: {stream_name}")
            lines.extend(
                [
                    *stream_setup,
                    f"                Py::new(py, Py{output_class} {{ {', '.join(constructor_fields)} }})",
                    "            })",
                    "        })",
                    "    }",
                ]
            )
            return "\n".join(lines)

        lines.append("                let result = PyDict::new(py);")
        for field in operation.output_fields:
            kind = self._kind(field.rust_type)
            if kind == "byte_stream":
                lines.append(
                    f"                let stream = Py::new(py, PyByteStream::new(output.{field.rust_name}))?;"
                )
                lines.append(f'                result.set_item("{field.name}", stream)?;')
                continue
            if kind == "event_receiver":
                arguments = _generic(field.rust_type, "crate::event_receiver::EventReceiver")
                if arguments is None or len(arguments) != 2:
                    raise GenerationError(f"invalid event receiver: {field.rust_type}")
                event_name = _crate_type_name(arguments[0])
                if event_name is None:
                    raise GenerationError(f"invalid event type: {arguments[0]}")
                constructor = f'Py::new(py, Py{event_name}Receiver::new(stream, "{smithy.name}"))'
                expression = (
                    f"output.{field.rust_name}.map(|stream| {constructor}).transpose()?"
                    if field.optional
                    else constructor.replace("new(stream,", f"new(output.{field.rust_name},") + "?"
                )
                lines.append(f"                let stream = {expression};")
                lines.append(f'                result.set_item("{field.name}", stream)?;')
                continue
            converted = self._next("converted")
            if field.optional:
                lines.append(f"                if let Some(value) = &output.{field.rust_name} {{")
                lines.extend(
                    self._to_py(field.rust_type, "value", converted, "                    ")
                )
                lines.append(f'                    result.set_item("{field.name}", {converted})?;')
                lines.append("                } else {")
                lines.append(f'                    result.set_item("{field.name}", py.None())?;')
                lines.append("                }")
            else:
                lines.extend(
                    self._to_py(
                        field.rust_type,
                        f"&output.{field.rust_name}",
                        converted,
                        "                ",
                    )
                )
                lines.append(f'                result.set_item("{field.name}", {converted})?;')
        lines.extend(
            [
                "                Ok(result.into_any().unbind())",
                "            })",
                "        })",
                "    }",
            ]
        )
        return "\n".join(lines)

    def _render_rust(self) -> str:
        converters: list[str] = []
        for rust in sorted(self.crate.types.values(), key=lambda item: item.name):
            shape = self._shape_for_rust(rust.name)
            if rust.is_struct:
                converters.extend([self._struct_from_py(rust), "", self._struct_to_py(rust), ""])
            elif shape is not None and shape.kind == "union":
                converters.extend(
                    [self._union_from_py(rust, shape), "", self._union_to_py(rust, shape), ""]
                )

        methods = [
            self._operation_method(operation.rust_name) for operation in self.service.operations
        ]
        native_structs = self._native_structs()
        native_outputs = self._native_output_operations()
        native_class_code = [self._native_struct_class(rust) for rust in native_structs]
        native_class_code.extend(
            self._native_operation_class(metadata) for metadata in native_outputs
        )
        native_classes = [f"Py{rust.name}" for rust in native_structs]
        native_classes.extend(metadata["rust_class"] for metadata in native_outputs)
        return self.templates.get_template("generated.rs.j2").render(
            converters="\n".join(converters),
            native_class_code="\n\n".join(native_class_code),
            native_classes=native_classes,
            methods="\n\n".join(methods),
            client_class=self.descriptor.native_class,
            python_package=self.descriptor.python_package,
            event_streams=self._event_streams(),
        )

    def _render_rust_runtime(self) -> str:
        return self.templates.get_template("lib.rs.j2").render(
            rust_module=self.rust_module,
            client_class=self.descriptor.native_class,
            python_package=self.descriptor.python_package,
            service_error_class=(self.descriptor.native_class.removesuffix("Client") + "Error"),
            force_path_style=self.descriptor.force_path_style_for_custom_endpoint,
        )

    def _render_cargo(self) -> str:
        return render_cargo(self.descriptor)

    def _render_pyproject(self) -> str:
        return render_pyproject(self.descriptor, self.repository_root)

    def _render_package_init(self) -> str:
        return self.templates.get_template("package_init.py.j2").render(
            client_class=self.descriptor.client_class,
            native_client_class=self.descriptor.native_class,
            event_streams=self._event_streams(),
            native_output_names=[
                metadata["class_name"] for metadata in self._native_output_operations()
            ],
        )

    def _render_facade(self) -> str:
        return self.templates.get_template("services.py.j2").render(services=list_services())

    def _render_core_init(self) -> str:
        return self.templates.get_template("core_init.py.j2").render(services=list_services())

    def _python_type(self, target: str, *, output: bool = False) -> str:
        target = target.replace("smithy.api#Primitive", "smithy.api#")
        builtin = {
            "smithy.api#String": "str",
            "smithy.api#Boolean": "bool",
            "smithy.api#Byte": "int",
            "smithy.api#Short": "int",
            "smithy.api#Integer": "int",
            "smithy.api#Long": "int",
            "smithy.api#Float": "float",
            "smithy.api#Double": "float",
            "smithy.api#BigDecimal": "float",
            "smithy.api#Blob": "bytes",
            "smithy.api#Timestamp": "str",
            "smithy.api#Document": "JSONValue",
            "smithy.api#Unit": "None",
        }
        if target in builtin:
            return builtin[target]
        shape = self.service.shapes[target]
        if shape.streaming and shape.kind == "union":
            name = self._output_type_name(shape) if output else shape.name
            return f"AsyncIterator[{name}]"
        if shape.kind in {"string", "enum"}:
            if shape.enum_values:
                return self._output_type_name(shape) if output else shape.name
            return "str"
        if shape.kind in {"structure", "union"}:
            return self._output_type_name(shape) if output else shape.name
        if shape.kind == "document":
            return "JSONValue"
        if shape.kind in {"list", "set"} and shape.member_target:
            nullable = " | None" if shape.sparse else ""
            collection = "list" if output else self._sequence_type
            return f"{collection}[{self._python_type(shape.member_target, output=output)}{nullable}]"
        if shape.kind == "map" and shape.key_target and shape.value_target:
            return (
                f"dict[{self._python_type(shape.key_target, output=output)}, "
                f"{self._python_type(shape.value_target, output=output)}"
                f"{' | None' if shape.sparse else ''}]"
            )
        if shape.kind == "blob":
            return "ByteStream" if shape.streaming and output else "bytes"
        if shape.kind == "timestamp":
            return "str"
        if shape.kind == "boolean":
            return "bool"
        if shape.kind in {"byte", "short", "integer", "long", "bigInteger"}:
            return "int"
        if shape.kind in {"float", "double", "bigDecimal"}:
            return "float"
        raise GenerationError(f"unsupported Python Smithy type: {shape.kind} ({target})")

    def _python_input_type(self, target: str) -> str:
        shape = self.service.shapes.get(target)
        if shape is not None and shape.kind == "blob" and shape.streaming:
            return "bytes"
        return self._python_type(target)

    def _rust_output_annotation(self, rust_type: str, optional: bool = False) -> str:
        kind = self._kind(rust_type)
        if kind in {"string", "datetime"}:
            annotation = "str"
        elif kind == "enum":
            shape = self._shape_for_rust(_crate_type_name(rust_type) or "")
            if shape is None or not shape.enum_values:
                raise GenerationError(f"missing Smithy enum for {rust_type}")
            annotation = f"_types.{self._output_type_name(shape)}"
        elif kind == "bool":
            annotation = "bool"
        elif kind in {"i8", "i16", "i32", "i64"}:
            annotation = "int"
        elif kind in {"f32", "f64"}:
            annotation = "float"
        elif kind == "document":
            annotation = "JSONValue"
        elif kind in {"box", "option"}:
            prefix = "::std::boxed::Box" if kind == "box" else "::std::option::Option"
            arguments = _generic(rust_type, prefix)
            assert arguments is not None
            annotation = self._rust_output_annotation(arguments[0], kind == "option")
        elif kind == "blob":
            annotation = "bytes"
        elif kind == "struct":
            annotation = _crate_type_name(rust_type) or "object"
        elif kind == "union":
            name = _crate_type_name(rust_type)
            shape = self._shape_for_rust(name or "")
            if shape is None:
                raise GenerationError(f"missing Smithy union for {rust_type}")
            annotation = f"_types.{self._output_type_name(shape)}"
        elif kind == "vec":
            arguments = _generic(rust_type, "::std::vec::Vec")
            if arguments is None:
                raise GenerationError(f"invalid Vec type: {rust_type}")
            annotation = f"list[{self._rust_output_annotation(arguments[0])}]"
        elif kind == "map":
            arguments = _generic(rust_type, "::std::collections::HashMap")
            if arguments is None or len(arguments) != 2:
                raise GenerationError(f"invalid HashMap type: {rust_type}")
            annotation = (
                f"dict[{self._rust_output_annotation(arguments[0])}, "
                f"{self._rust_output_annotation(arguments[1])}]"
            )
        elif kind == "byte_stream":
            annotation = "ByteStream"
        else:
            annotation = "object"
        return f"{annotation} | None" if optional else annotation

    def _native_stub_classes(self) -> list[dict[str, object]]:
        classes: list[dict[str, object]] = []
        for rust in self._native_structs():
            classes.append(
                {
                    "name": rust.name,
                    "fields": [
                        {
                            "name": _safe_parameter(field.rust_name.removeprefix("r#")),
                            "annotation": self._rust_output_annotation(
                                field.rust_type, field.optional
                            ),
                        }
                        for field in rust.fields
                    ],
                }
            )
        for metadata in self._native_output_operations():
            operation = self.crate.operations[metadata["operation"]]
            classes.append(
                {
                    "name": metadata["class_name"],
                    "fields": [
                        {
                            "name": _safe_parameter(field.rust_name.removeprefix("r#")),
                            "annotation": self._rust_output_annotation(
                                field.rust_type, field.optional
                            ),
                        }
                        for field in operation.output_fields
                    ],
                }
            )
        return classes

    def _render_python_types(self) -> str:
        imports = [
            "# Generated by rboto-codegen. DO NOT EDIT.",
            "from __future__ import annotations",
            "",
            "from collections.abc import AsyncIterator, "
            + ("Sequence as _Sequence" if self._sequence_type == "_Sequence" else "Sequence"),
            "from typing import Literal, NotRequired, Required, TypeAlias, TypedDict",
            "",
            "from rboto.types import UnknownEnumValue",
            "",
            "from . import _native",
            "",
        ]
        lines = [
            "type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]",
            "",
        ]
        reserved = {
            "AsyncIterator", self._sequence_type, "Literal", "NotRequired", "Required",
            "TypeAlias", "TypedDict", "UnknownEnumValue", "ByteStream", "JSONValue",
        }
        shapes = sorted(self.service.shapes.values(), key=lambda shape: shape.name)
        native_operations = {metadata["operation"] for metadata in self._native_output_operations()}
        native_output_targets = {
            operation.output_target
            for operation in self.service.operations
            if operation.rust_name in native_operations
            and operation.output_target not in self._input_targets
        }
        for shape in shapes:
            if shape.kind in {"string", "enum"} and shape.enum_values:
                values = ", ".join(repr(value) for value in shape.enum_values)
                lines.append(f"{shape.name}: TypeAlias = Literal[{values}]")
                lines.append(
                    f"{self._output_type_name(shape)}: TypeAlias = {shape.name} | UnknownEnumValue"
                )
                reserved.update((shape.name, self._output_type_name(shape)))
        lines.append("")
        for shape in shapes:
            if shape.error or shape.kind not in {"structure", "union"}:
                continue
            contexts: list[bool] = []
            if shape.shape_id not in native_output_targets:
                contexts.append(
                    shape.shape_id in self._dictionary_output_targets
                    and shape.shape_id not in self._input_targets
                )
            if (
                shape.shape_id in self._dictionary_output_targets
                and (not contexts or not contexts[0])
            ):
                contexts.append(True)
            for output in contexts:
                lines.extend(self._python_shape_definition(shape, output=output))
                name = self._output_type_name(shape) if output else shape.name
                reserved.add(name)
                if shape.kind == "union":
                    reserved.update(f"{name}{member.name}" for member in shape.members)
                    if output:
                        reserved.add(f"{name}Unknown")

        # Keep every existing mapping and alias intact. Native nested structures
        # often share a name with an input TypedDict, so expose those as *Output.
        exports = [("ByteStream", "ByteStream")]
        native_names = [
            *(item["class_name"] for item in self._native_output_operations()),
            *(stream["receiver_class"] for stream in self._event_streams()),
            *(rust.name for rust in self._native_structs()),
        ]
        for native_name in dict.fromkeys(native_names):
            public_name = native_name
            if public_name in reserved:
                public_name += "Response" if public_name.endswith("Output") else "Output"
                # Avoid taking another native class's canonical public name.
                while public_name in reserved or public_name in native_names:
                    public_name += "Value"
            reserved.add(public_name)
            exports.append((native_name, public_name))
        imports.extend([
            *(f"{public}: TypeAlias = _native.{native}" for native, public in sorted(exports)),
            "",
        ])
        return "\n".join([*imports, *lines])

    def _python_shape_definition(self, shape: SmithyShape, *, output: bool) -> list[str]:
        lines: list[str] = []
        name = self._output_type_name(shape) if output else shape.name
        if shape.kind == "structure":
            entries: list[str] = []
            for member in shape.members:
                # Concrete SDK spellings handle names such as A2A, GiB, and MD5.
                field = self._member_field(shape, member)
                if output and field is None:
                    raise GenerationError(f"missing output field: {shape.name}.{member.name}")
                member_name = field.name if field is not None else member.rust_name
                value_type = self._python_type(member.target, output=output)
                if output and field is not None and field.optional:
                    value_type += " | None"
                wrapper = "Required" if output or member.required else "NotRequired"
                entries.append(f'    "{member_name}": {wrapper}["{value_type}"],')
            lines.append(f"{name} = TypedDict(")
            lines.append(f'    "{name}",')
            lines.append("    {")
            lines.extend(entries)
            lines.append("    },")
            lines.append(")")
            lines.append("")
        elif shape.kind == "union":
            variants: list[str] = []
            for member in shape.members:
                if (
                    self.service.shapes.get(member.target) is not None
                    and self.service.shapes[member.target].error
                ):
                    continue
                variant = f"{name}{member.name}"
                lines.append(
                    f'{variant} = TypedDict("{variant}", '
                    f'{{"{member.rust_name}": '
                    f'Required["{self._python_type(member.target, output=output)}"]}})'
                )
                variants.append(variant)
            if output:
                variants.append(f"{name}Unknown")
                lines.append(
                    f'{name}Unknown = TypedDict("{name}Unknown", '
                    '{"unknown": Required[Literal[True]]})'
                )
            lines.append(f"{name}: TypeAlias = {' | '.join(variants)}")
            lines.append("")
        return lines

    def _method_signature(self, operation_name: str) -> dict[str, object]:
        smithy = next(op for op in self.service.operations if op.rust_name == operation_name)
        input_shape = (
            self.service.shapes[smithy.input_target]
            if smithy.input_target not in {None, "smithy.api#Unit"}
            and smithy.input_target is not None
            else None
        )
        members = list(input_shape.members) if input_shape else []
        members.sort(key=lambda member: not member.required)
        parameters: list[dict[str, object]] = []
        for member in members:
            parameter = _safe_parameter(member.rust_name)
            annotation = self._python_input_type(member.target)
            parameters.append(
                {
                    "name": parameter,
                    "key": member.rust_name,
                    "annotation": annotation,
                    "required": member.required,
                }
            )
        native_output = False
        if smithy.output_target is None or smithy.output_target == "smithy.api#Unit":
            output = "None"
        elif metadata := next(
            (
                metadata
                for metadata in self._native_output_operations()
                if metadata["operation"] == operation_name
            ),
            None,
        ):
            output = metadata["class_name"]
            native_output = True
        else:
            output = self._output_type_name(self.service.shapes[smithy.output_target])
        return {
            "name": operation_name,
            "operation_name": smithy.name,
            "parameters": parameters,
            "output": output,
            "native_output": native_output,
        }

    def _render_python_client(self, *, paginators: bool = False) -> str:
        names = (
            [item.operation for item in self.pagination] if paginators
            else [op.rust_name for op in self.service.operations]
        )
        methods = [self._method_signature(name) for name in names]
        type_names: set[str] = set()
        ignored = {
            "str", "int", "bool", "float", "bytes", "list", "dict", "None",
            "AsyncIterator", self._sequence_type,
        }
        native_output_names = {
            metadata["class_name"] for metadata in self._native_output_operations()
        }
        if paginators:
            native_output_names.intersection_update(str(method["output"]) for method in methods)
        for method in methods:
            output = str(method["output"])
            if output not in native_output_names:
                type_names.update(
                    token
                    for token in re.findall(r"\b[A-Za-z_]\w*\b", output)
                    if token not in ignored
                )
            raw_parameters = method["parameters"]
            if not isinstance(raw_parameters, list):
                raise GenerationError("method parameters must be a list")
            for raw_parameter in cast(list[object], raw_parameters):
                if not isinstance(raw_parameter, dict):
                    raise GenerationError("method parameter must be a dictionary")
                parameter = cast(dict[str, object], raw_parameter)
                annotation = str(parameter["annotation"])
                type_names.update(
                    token
                    for token in re.findall(r"\b[A-Za-z_]\w*\b", annotation)
                    if token not in ignored
                )
        return (
            self.templates.get_template("paginators.py.j2" if paginators else "client.py.j2")
            .render(
                methods=methods,
                type_names=sorted(type_names),
                native_output_names=sorted(native_output_names),
                native_outputs=self.descriptor.native_outputs,
                client_class=self.descriptor.client_class,
                native_client_class=self.descriptor.native_class,
                service_id=self.descriptor.service_id,
                sequence_type=self._sequence_type,
                pagination={
                    item.operation: {
                        "input_tokens": repr(item.input_tokens),
                        "output_tokens": repr(item.output_tokens),
                        "more_results": repr(item.more_results),
                    }
                    for item in self.pagination
                },
            )
            .rstrip()
            + "\n"
        )

    def _render_native_stub(self) -> str:
        methods = [self._method_signature(op.rust_name) for op in self.service.operations]
        fallback_types = sorted(
            {
                str(method["output"])
                for method in methods
                if method["output"] != "None" and not method["native_output"]
            }
        )
        return (
            self.templates.get_template("native.pyi.j2")
            .render(
                methods=methods,
                client_class=self.descriptor.native_class,
                event_streams=[
                    {**stream, "event_type": self._output_type_name(
                        self._shapes_by_name[stream["event_name"]]
                    )}
                    for stream in self._event_streams()
                ],
                native_classes=self._native_stub_classes(),
                native_outputs=self.descriptor.native_outputs,
                fallback_types=fallback_types,
            )
            .rstrip()
            + "\n"
        )

    def _render_exceptions(self) -> str:
        # Streaming errors can be union members without appearing in operation.errors.
        errors = sorted(
            {shape.name for shape in self.service.shapes.values() if shape.error}
        )
        lines = [
            "# Generated by rboto-codegen. DO NOT EDIT.",
            "from rboto.exceptions import ServiceError as BaseServiceError",
            "",
            "",
            f"class {self.descriptor.native_class.removesuffix('Client')}Error(BaseServiceError):",
            f'    """Base exception for errors returned by {self.descriptor.service_id}."""',
            "",
        ]
        for error in errors:
            stem = error.removesuffix("Exception").removesuffix("Error")
            lines.extend(
                [
                    "",
                    f"class {stem}Error({self.descriptor.native_class.removesuffix('Client')}Error):",
                    f'    """Modeled {self.descriptor.service_id} error: {error}."""',
                    "",
                ]
            )
        return "\n".join(lines)


def generate_service(
    descriptor: ServiceDescriptor, model_path: Path, repository_root: Path
) -> GeneratedPaths:
    return ServiceGenerator(descriptor, model_path, repository_root).generate()
