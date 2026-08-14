from __future__ import annotations

from dataclasses import dataclass

from .rust_crate import RustCrate
from .smithy import SmithyService


@dataclass(frozen=True, slots=True)
class OperationAlignment:
    name: str
    smithy_only_inputs: tuple[str, ...]
    rust_only_inputs: tuple[str, ...]
    smithy_only_outputs: tuple[str, ...]
    rust_only_outputs: tuple[str, ...]

    @property
    def aligned(self) -> bool:
        return not (
            self.smithy_only_inputs
            or self.rust_only_inputs
            or self.smithy_only_outputs
            or self.rust_only_outputs
        )


@dataclass(frozen=True, slots=True)
class AlignmentReport:
    aligned: tuple[OperationAlignment, ...]
    mismatched: tuple[OperationAlignment, ...]
    smithy_only_operations: tuple[str, ...]
    rust_only_operations: tuple[str, ...]


def align(service: SmithyService, crate: RustCrate) -> AlignmentReport:
    smithy_operations = {operation.rust_name: operation for operation in service.operations}
    common = sorted(set(smithy_operations) & set(crate.operations))
    results: list[OperationAlignment] = []

    for name in common:
        smithy_operation = smithy_operations[name]
        rust_operation = crate.operations[name]

        def smithy_members(target: str | None) -> set[str]:
            if target is None or target == "smithy.api#Unit":
                return set()
            shape = service.shapes.get(target)
            if shape is None:
                raise ValueError(f"operation references unknown shape: {target}")
            return {member.rust_name for member in shape.members}

        smithy_inputs = smithy_members(smithy_operation.input_target)
        smithy_outputs = smithy_members(smithy_operation.output_target)
        rust_inputs = {field.name for field in rust_operation.input_fields}
        rust_outputs = {field.name for field in rust_operation.output_fields}
        results.append(
            OperationAlignment(
                name=name,
                smithy_only_inputs=tuple(sorted(smithy_inputs - rust_inputs)),
                rust_only_inputs=tuple(sorted(rust_inputs - smithy_inputs)),
                smithy_only_outputs=tuple(sorted(smithy_outputs - rust_outputs)),
                rust_only_outputs=tuple(sorted(rust_outputs - smithy_outputs)),
            )
        )

    return AlignmentReport(
        aligned=tuple(result for result in results if result.aligned),
        mismatched=tuple(result for result in results if not result.aligned),
        smithy_only_operations=tuple(sorted(set(smithy_operations) - set(crate.operations))),
        rust_only_operations=tuple(sorted(set(crate.operations) - set(smithy_operations))),
    )
