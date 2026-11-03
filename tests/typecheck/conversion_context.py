"""The optional error metadata can be consumed without Any or casts."""

from typing import assert_type

from rboto.exceptions import (
    ConversionErrorCategory,
    ConversionErrorContext,
    get_conversion_context,
)


def inspect_error(error: BaseException) -> str | None:
    context = get_conversion_context(error)
    assert_type(context, ConversionErrorContext | None)
    if context is None:
        return None
    assert_type(context.path, str)
    assert_type(context.expected, str)
    assert_type(context.category, ConversionErrorCategory)
    assert_type(context.truncated, bool)
    return f"{context.service}.{context.operation}: {context.path}"
