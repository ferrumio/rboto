"""Generate native CLI adapters from the same descriptors, models and Rust parser."""

from __future__ import annotations

import html
import json
import re
import tomllib
from pathlib import Path

from .generator import (
    EVENT_SENDER,
    GenerationError,
    ServiceGenerator,
    crate_type_name,
    generic_arguments,
    write_generated,
)
from .manifests import template_environment
from .model import ServiceDescriptor
from .registry import list_services
from .rust_crate import RustField, RustOperation, RustType
from .smithy import SmithyOperation, SmithyShape, to_snake_case


def _strings(values: list[str] | tuple[str, ...]) -> str:
    return "&" + json.dumps(values)


class CliServiceGenerator(ServiceGenerator):
    def __init__(self, descriptor: ServiceDescriptor, model_path: Path, root: Path) -> None:
        super().__init__(descriptor, model_path, root)
        self.descriptor = descriptor
        self.root = root
        self.module = descriptor.rust_module
        self.operations = {op.rust_name: op for op in self.service.operations}
        if set(self.operations) != set(self.crate.operations):
            raise GenerationError(f"{descriptor.service_id}: CLI operation/model mismatch")
        features = tomllib.loads((self.crate.path / "Cargo.toml").read_text())["features"]
        expected = set(features["default"]) - {"rustls", "legacy-https-client"}
        if set(descriptor.cli_features) != expected:
            raise GenerationError(
                f"{descriptor.service_id}: review cli_features after the SDK update"
            )

    def decode(self, rust_type: str, value: str = "value") -> str:
        kind = self._kind(rust_type)
        if kind == "string":
            return f"core::from_json::<String>({value})?"
        if kind in {"bool", "i8", "i16", "i32", "i64", "f32", "f64"}:
            return f"core::from_json::<{kind}>({value})?"
        if kind in {"document", "blob"}:
            return f"core::{kind}({value})?"
        if kind == "datetime":
            return f"core::parse_timestamp({value})?"
        if kind == "byte_stream":
            return f"core::byte_stream({value}).await?"
        if kind == "event_sender":
            args = generic_arguments(rust_type, EVENT_SENDER)
            assert args is not None and len(args) == 2
            event = crate_type_name(args[0])
            error = self._public_rust_type(args[1])
            return (
                f"core::event_stream({value}, {to_snake_case(event or '')}_from_json, "
                f"{error}::unhandled).await?.into()"
            )
        if kind == "box":
            args = generic_arguments(rust_type, "::std::boxed::Box")
            assert args is not None
            return f"Box::new({self.decode(args[0], value)})"
        if kind == "option":
            args = generic_arguments(rust_type, "::std::option::Option")
            assert args is not None
            return f"if {value}.is_null() {{ None }} else {{ Some({self.decode(args[0], value)}) }}"
        if kind == "enum":
            return (
                f"{self._public_rust_type(rust_type)}::from("
                f"core::from_json::<String>({value})?.as_str())"
            )
        if kind in {"struct", "union"}:
            return f"{to_snake_case(crate_type_name(rust_type) or '')}_from_json({value})?"
        if kind == "vec":
            args = generic_arguments(rust_type, "::std::vec::Vec")
            assert args is not None
            return (
                f"core::array({value})?.iter().map(|value| Ok({self.decode(args[0])}))"
                ".collect::<Result<Vec<_>>>()?"
            )
        if kind == "map":
            args = generic_arguments(rust_type, "::std::collections::HashMap")
            assert args is not None
            key = self.decode(args[0], "&Value::String(key.clone())")
            return (
                f"core::object({value})?.iter().map(|(key, value)| "
                f"Ok(({key}, {self.decode(args[1])})))"
                ".collect::<Result<HashMap<_, _>>>()?"
            )
        raise GenerationError(f"CLI cannot decode {rust_type}")

    def encode(self, rust_type: str, value: str = "value") -> str:
        kind = self._kind(rust_type)
        if kind in {"string", "bool", "i8", "i16", "i32", "i64", "f32", "f64"}:
            return f"serde_json::to_value({value})?"
        if kind == "document":
            return f"core::document_json({value})?"
        if kind == "datetime":
            return f"Value::String(({value}).to_string())"
        if kind == "blob":
            return f"core::bytes_json(({value}).as_ref())"
        if kind == "enum":
            return f"Value::String(({value}).as_str().to_owned())"
        if kind in {"struct", "union"}:
            return f"{to_snake_case(crate_type_name(rust_type) or '')}_to_json({value})?"
        if kind == "box":
            args = generic_arguments(rust_type, "::std::boxed::Box")
            assert args is not None
            return self.encode(args[0], f"({value}).as_ref()")
        if kind == "option":
            args = generic_arguments(rust_type, "::std::option::Option")
            assert args is not None
            return f"match {value} {{ Some(value) => {self.encode(args[0])}, None => Value::Null }}"
        if kind == "vec":
            args = generic_arguments(rust_type, "::std::vec::Vec")
            assert args is not None
            return (
                f"Value::Array(({value}).iter().map(|value| Ok({self.encode(args[0])}))"
                ".collect::<Result<Vec<_>>>()?)"
            )
        if kind == "map":
            args = generic_arguments(rust_type, "::std::collections::HashMap")
            assert args is not None
            key = "key.clone()" if self._kind(args[0]) == "string" else "key.as_str().to_owned()"
            return (
                f"Value::Object(({value}).iter().map(|(key, value)| "
                f"Ok(({key}, {self.encode(args[1])})))"
                ".collect::<Result<Map<_, _>>>()?)"
            )
        raise GenerationError(f"CLI cannot encode {rust_type}")

    def required(self, shape: SmithyShape | None, fields: tuple[RustField, ...]) -> list[str]:
        if shape is None:
            return []
        available = {field.name for field in fields}
        return [
            member.rust_name
            for member in shape.members
            if member.required and member.rust_name in available
        ]

    def structure(self, rust: RustType) -> str:
        name = to_snake_case(rust.name)
        shape = self._shape_for_rust(rust.name)
        lines = [
            f"fn {name}_from_json(value: &Value) -> Result<{self.module}::types::{rust.name}> {{",
            f"    core::check_fields(value, {_strings([f.name for f in rust.fields])}, {_strings(self.required(shape, rust.fields))})?;",
            "    let values = core::object(value)?;",
            f"    let mut builder = {self.module}::types::{rust.name}::builder();",
        ]
        for field in rust.fields:
            expression = self.decode(field.rust_type)
            if ".await" in expression:
                raise GenerationError(f"nested asynchronous input: {rust.name}.{field.name}")
            lines += [
                f'    if let Some(value) = core::optional(values, "{field.name}") {{',
                f"        let converted = (|| -> Result<_> {{ Ok({expression}) }})()",
                f'            .map_err(|error| error.at("{field.name}"))?;',
                f"        builder = builder.set_{field.name}(Some(converted));",
                "    }",
            ]
        lines += [
            "    builder.build().map_err(|error| CliError::input(error.to_string()))"
            if rust.build_fallible
            else "    Ok(builder.build())",
            "}",
            f"fn {name}_to_json(value: &{self.module}::types::{rust.name}) -> Result<Value> {{",
            "    let mut result = Map::new();",
        ]
        for field in rust.fields:
            lines.extend(self.output_field(field, "value"))
        lines += ["    Ok(Value::Object(result))", "}"]
        return "\n".join(lines)

    def output_field(self, field: RustField, source: str) -> list[str]:
        expr = self.encode(field.rust_type)
        if field.optional:
            return [
                f"    let converted = match &{source}.{field.rust_name} {{",
                f"        Some(value) => {expr}, None => Value::Null,",
                "    };",
                f'    result.insert("{field.name}".into(), converted);',
            ]
        return [
            f'    result.insert("{field.name}".into(), {self.encode(field.rust_type, f"&{source}.{field.rust_name}")});'
        ]

    def union(self, rust: RustType, shape: SmithyShape) -> str:
        name = to_snake_case(rust.name)
        path = f"{self.module}::types::{rust.name}"
        decode = [
            f"fn {name}_from_json(value: &Value) -> Result<{path}> {{",
            "    let values = core::object(value)?;",
            '    if values.len() != 1 { return Err(CliError::input("union requires exactly one variant")); }',
        ]
        encode = [
            f"fn {name}_to_json(value: &{path}) -> Result<Value> {{",
            "    let mut result = Map::new();",
            "    match value {",
        ]
        for member in shape.members:
            member_shape = self.service.shapes.get(member.target)
            if member_shape is not None and member_shape.error:
                continue
            variant = next(
                (
                    variant
                    for variant in rust.enum_variants
                    if variant.lower() == member.name.lower()
                ),
                member.name,
            )
            if member.target == "smithy.api#Unit":
                decode += [
                    f'    if let Some(value) = values.get("{member.rust_name}") {{',
                    '        if !value.is_null() { return Err(CliError::input("unit variant requires null")); }',
                    f"        return Ok({path}::{variant});",
                    "    }",
                ]
                encode += [
                    f'        {path}::{variant} => {{ result.insert("{member.rust_name}".into(), Value::Null); }}'
                ]
            else:
                rust_type = dict(rust.variant_types).get(variant)
                if rust_type is None:
                    raise GenerationError(f"CLI missing Rust union payload: {rust.name}.{variant}")
                decode += [
                    f'    if let Some(value) = values.get("{member.rust_name}") {{',
                    f"        return Ok({path}::{variant}({self.decode(rust_type)}));",
                    "    }",
                ]
                encode += [
                    f'        {path}::{variant}(value) => {{ result.insert("{member.rust_name}".into(), {self.encode(rust_type)}); }}'
                ]
        decode += [f'    Err(CliError::input("unknown variant for {rust.name}"))', "}"]
        encode += [
            '        _ => { result.insert("unknown".into(), Value::Bool(true)); }',
            "    }",
            "    Ok(Value::Object(result))",
            "}",
        ]
        return "\n".join(decode + encode)

    def paginated(self, operation: str) -> bool:
        path = self.crate.path / "src/operation" / operation / "builders.rs"
        return "pub fn into_paginator(" in path.read_text()

    def handler(self, operation: RustOperation, smithy: SmithyOperation) -> str:
        name = operation.name
        shape = self.service.shapes.get(smithy.input_target or "")
        binary = [
            f.name
            for f in operation.output_fields
            if self._kind(f.rust_type) in {"blob", "byte_stream"}
        ]
        streaming = any(self._kind(f.rust_type) == "byte_stream" for f in operation.output_fields)
        has_events = any(
            self._kind(f.rust_type) == "event_receiver" for f in operation.output_fields
        )
        lines = [
            f"async fn call_{name}(client: &{self.module}::Client, input: &Value, sink: &Output) -> Result<()> {{",
            f"    core::check_fields(input, {_strings([f.name for f in operation.input_fields])}, {_strings(self.required(shape, operation.input_fields))})?;",
            f"    sink.options.validate_output({_strings(binary)}, {str(streaming).lower()})?;",
        ]
        if not self.paginated(name):
            lines += [
                '    if sink.options.paginate { return Err(CliError::input("this operation has no paginator")); }'
            ]
        if has_events:
            lines += [
                '    if sink.options.pretty { return Err(CliError::input("event streams use compact NDJSON; omit --pretty")); }'
            ]
        lines += ["    let values = core::object(input)?;"]
        for field in operation.input_fields:
            lines += [
                f'    let {field.name}_value = if let Some(value) = core::optional(values, "{field.name}") {{',
                f"        Some({self.decode(field.rust_type)})",
                "    } else { None };",
            ]
        lines += [f"    let request = client.{name}()"]
        for field in operation.input_fields:
            lines += [f"        .set_{field.name}({field.name}_value)"]
        lines[-1] += ";"
        if self.paginated(name):
            lines += [
                "    if sink.options.paginate {",
                "        let mut pages = request.into_paginator().send();",
                "        let mut count = 0;",
                "        while let Some(output) = pages.next().await {",
                f'            let output = output.map_err(|error| core::api_error(error, "{self.descriptor.service_id}", "{smithy.name}"))?;',
                f"            render_{name}(output, sink).await?;",
                "            count += 1;",
                "            if sink.options.max_pages.is_some_and(|limit| count >= limit) { break; }",
                "        }",
                "        return Ok(());",
                "    }",
            ]
        lines += [
            f'    let output = request.send().await.map_err(|error| core::api_error(error, "{self.descriptor.service_id}", "{smithy.name}"))?;',
            f"    render_{name}(output, sink).await",
            "}",
            f"async fn render_{name}(output: {self.module}::operation::{name}::{operation.output_type}, sink: &Output) -> Result<()> {{",
            "    let mut result = Map::new();",
        ]
        for field in operation.output_fields:
            kind = self._kind(field.rust_type)
            if kind in {"event_receiver", "byte_stream"}:
                continue
            if kind == "blob":
                expr = f'sink.bytes("{field.name}", value.as_ref()).await?'
                if field.optional:
                    lines += [
                        f"    let converted = match &output.{field.rust_name} {{ Some(value) => {expr}, None => Value::Null }};"
                    ]
                else:
                    lines += [
                        f"    let value = &output.{field.rust_name};",
                        f"    let converted = {expr};",
                    ]
                lines += [f'    result.insert("{field.name}".into(), converted);']
            else:
                lines.extend(self.output_field(field, "output"))
        for field in operation.output_fields:
            if self._kind(field.rust_type) != "byte_stream":
                continue
            expr = f'sink.stream("{field.name}", stream).await?'
            if field.optional:
                lines += [
                    f"    let converted = match output.{field.rust_name} {{ Some(stream) => {expr}, None => Value::Null }};"
                ]
            else:
                lines += [
                    f"    let stream = output.{field.rust_name};",
                    f"    let converted = {expr};",
                ]
            lines += [f'    result.insert("{field.name}".into(), converted);']
        if has_events:
            for field in operation.output_fields:
                if self._kind(field.rust_type) == "event_receiver":
                    lines += [
                        f'    result.insert("{field.name}".into(), core::json!({{"stream": "events"}}));'
                    ]
        lines += ["    sink.emit(&Value::Object(result)).await?;"]
        for field in operation.output_fields:
            if self._kind(field.rust_type) != "event_receiver":
                continue
            args = generic_arguments(field.rust_type, "crate::event_receiver::EventReceiver")
            assert args is not None
            event = crate_type_name(args[0])
            stream_meta = next(
                row
                for row in self._event_streams()
                if row["rust_type"] == self._public_rust_type(field.rust_type)
            )
            lines += [
                f"    {'if let Some(mut stream)' if field.optional else 'let mut stream'} = output.{field.rust_name}"
                + (" {" if field.optional else ";"),
                "    loop {",
                "        let event = stream.recv().await.map_err(|error| {",
                "            let code = match error.as_service_error() {",
                stream_meta["error_arms"],
                "                _ => None,",
                "            };",
                f'            core::stream_error(error, "{self.descriptor.service_id}", "{smithy.name}", code)',
                "        })?;",
                "        let Some(event) = event else { break; };",
                f"        let event = {to_snake_case(event or '')}_to_json(&event)?;",
                f'        sink.emit(&core::json!({{"stream": "{field.name}", "event": event}})).await?;',
                "    }",
            ]
            if field.optional:
                lines += ["    }"]
        lines += ["    Ok(())", "}"]
        return "\n".join(lines)

    def catalog(self) -> dict[str, object]:
        operations: list[dict[str, object]] = []
        for name, operation in sorted(self.crate.operations.items()):
            smithy = self.operations[name]
            shape = self.service.shapes.get(smithy.input_target or "")
            required = self.required(shape, operation.input_fields)
            params: list[dict[str, object]] = []
            for field in operation.input_fields:
                kind = self._kind(field.rust_type)
                params.append(
                    {
                        "name": field.name,
                        "kind": kind,
                        "required": field.name in required,
                        "type": crate_type_name(field.rust_type) or kind,
                    }
                )
            operations.append(
                {
                    "name": name,
                    "api_name": smithy.name,
                    "parameters": params,
                    "paginated": self.paginated(name),
                    "description": html.unescape(
                        re.sub("<[^>]+>", "", smithy.documentation)
                    ).strip()[:1000],
                }
            )
        return {"service": self.descriptor.service_id, "operations": operations}

    def generate_cli_service(self) -> tuple[Path, ...]:
        root = self.root / "cli/services" / self.descriptor.service_id
        (root / "src").mkdir(parents=True, exist_ok=True)
        converters: list[str] = []
        for rust in sorted(self.crate.types.values(), key=lambda rust: rust.name):
            shape = self._shape_for_rust(rust.name)
            if rust.is_struct:
                converters.append(self.structure(rust))
            elif shape is not None and shape.kind == "union":
                converters.append(self.union(rust, shape))
        handlers = [
            self.handler(operation, self.operations[name])
            for name, operation in sorted(self.crate.operations.items())
        ]
        rust_path = root / "src/lib.rs"
        content = self.templates.get_template("cli_service.rs.j2").render(
            operations=sorted(self.operations),
            converters="\n\n".join(converters),
            handlers="\n\n".join(handlers),
            rust_module=self.module,
            service_id=self.descriptor.service_id,
            path_style=self.descriptor.force_path_style_for_custom_endpoint,
        )
        write_generated(rust_path, content)
        cargo = generate_cli_manifest(self.descriptor, self.root)
        return rust_path, cargo


def generate_cli_manifest(descriptor: ServiceDescriptor, root: Path) -> Path:
    selected = sorted(descriptor.cli_features)
    if not {"default-https-client", "rt-tokio"} <= set(selected):
        raise GenerationError(f"{descriptor.service_id}: review the CLI transport features")
    path = root / "cli/services" / descriptor.service_id / "Cargo.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_generated(
        path,
        template_environment()
        .get_template("cli_service.Cargo.toml.j2")
        .render(
            service_id=descriptor.service_id,
            rust_crate=descriptor.rust_crate,
            rust_version=descriptor.rust_crate_version,
            features=json.dumps(selected),
        ),
    )
    return path


def generate_cli_layout(root: Path) -> tuple[Path, ...]:
    """Bootstrap CLI pins without requiring models or downloaded Rust crates."""
    services = list_services()
    templates = template_environment()
    workspace = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]
    app = root / "cli/app"
    (app / "src").mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for template, path in [
        ("cli_workspace.Cargo.toml.j2", root / "cli/Cargo.toml"),
        ("cli_app.Cargo.toml.j2", app / "Cargo.toml"),
        ("cli_dispatch.rs.j2", app / "src/generated.rs"),
    ]:
        write_generated(
            path,
            templates.get_template(template).render(
                services=services,
                workspace=workspace,
            ),
        )
        paths.append(path)
    for descriptor in services:
        paths.append(generate_cli_manifest(descriptor, root))
    return tuple(paths)


def generate_cli(root: Path) -> tuple[Path, ...]:
    services = list_services()
    paths: list[Path] = list(generate_cli_layout(root))
    catalog: list[dict[str, object]] = []
    for descriptor in services:
        generator = CliServiceGenerator(
            descriptor,
            root / "codegen/models" / f"{descriptor.service_id}.json",
            root,
        )
        paths.extend(generator.generate_cli_service())
        catalog.append(generator.catalog())
    app = root / "cli/app"
    (app / "src").mkdir(parents=True, exist_ok=True)
    catalog_path = app / "src/catalog.json"
    write_generated(
        catalog_path, json.dumps(catalog, separators=(",", ":"), ensure_ascii=False) + "\n"
    )
    paths.append(catalog_path)
    return tuple(paths)
