"""Allocate distinct Python names for modeled service exceptions."""

from collections.abc import Iterable


def normalized_error_name(name: str) -> str:
    return name.removesuffix("Exception").removesuffix("Error") + "Error"


def error_class_names(names: Iterable[str], service_base: str) -> dict[str, str]:
    ordered = sorted(set(names))
    preferred = {name: normalized_error_name(name) for name in ordered}
    reserved = {*preferred.values(), service_base, "BaseServiceError"}
    allocated = {service_base, "BaseServiceError"}
    result: dict[str, str] = {}
    for name in ordered:
        candidate = preferred[name]
        if candidate in allocated:
            candidate = name if name.endswith("Error") else f"{name}Error"
            while candidate in reserved:
                candidate = candidate.removesuffix("Error") + "ValueError"
        result[name] = candidate
        allocated.add(candidate)
        reserved.add(candidate)
    return result
