"""Shared types for service values."""

from typing import NewType

# SDK enum values added after the installed service model remain ordinary strings
# at runtime. The nominal type keeps arbitrary user strings out of known enum
# inputs and dictionary keys without rejecting future values in responses.
UnknownEnumValue = NewType("UnknownEnumValue", str)
