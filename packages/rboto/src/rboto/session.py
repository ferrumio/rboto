from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Session:
    """Configuration shared when constructing rboto service clients."""

    region: str | None = None
    profile: str | None = None
    endpoint_url: str | None = None
