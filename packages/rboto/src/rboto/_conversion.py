"""Failure-only construction of public input conversion diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ConversionErrorCategory = Literal[
    "type", "value", "missing", "union", "overflow", "iteration", "unknown_parameter",
    "unknown_field",
]


@dataclass(frozen=True, slots=True)
class ConversionErrorContext:
    """Bounded schema metadata about an input conversion failure."""

    service: str
    operation: str
    path: str
    expected: str
    category: ConversionErrorCategory
    truncated: bool = False


def get_conversion_context(error: BaseException) -> ConversionErrorContext | None:
    """Read conversion metadata without assuming a new exception superclass."""
    context: object = getattr(error, "conversion_context", None)
    return context if isinstance(context, ConversionErrorContext) else None


def _bound(value: str, length: int) -> str:
    return value if len(value) <= length else value[:length - 3] + "..."


def attach_conversion_context(
    error: BaseException,
    service: str,
    operation: str,
    path: str,
    expected: str,
    category: ConversionErrorCategory,
    truncated: bool = False,
) -> BaseException:
    """Called only after failure, from native converters or middleware validation."""
    context = ConversionErrorContext(
        service=_bound(service, 128),
        operation=_bound(operation, 128),
        path=_bound(path or "<request>", 512),
        expected=_bound(expected, 160),
        category=category,
        truncated=truncated or len(path) > 512 or len(expected) > 160
        or len(service) > 128 or len(operation) > 128,
    )
    message = (
        f"{context.path}: expected {context.expected} "
        f"({context.service}.{context.operation}; {context.category})"
    )
    # Custom Python errors can carry application-specific attributes and causes.
    # Keep that contract, without reading or echoing their messages in our note.
    if (
        type(error) in (TypeError, ValueError, OverflowError)
        and error.__traceback__ is None and error.__cause__ is None
        and error.__context__ is None
    ):
        error.args = (message,)
    else:
        error.add_note(message)
    setattr(error, "conversion_context", context)
    return error
