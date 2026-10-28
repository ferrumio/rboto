from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, TypeAdapter


@dataclass(frozen=True, slots=True)
class ToolInput:
    key: Annotated[str, Field(strict=True, min_length=1)]


# Construct once and reuse the adapter across tool calls.
input_adapter = TypeAdapter(ToolInput)
input_schema = input_adapter.json_schema()


def parse_arguments(arguments_json: str) -> ToolInput:
    return input_adapter.validate_json(arguments_json)
