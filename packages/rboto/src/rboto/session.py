from dataclasses import dataclass

from .config import ClientConfig


@dataclass(frozen=True, slots=True)
class Session:
    """Configuration shared when constructing rboto service clients."""

    region: str | None = None
    profile: str | None = None
    endpoint_url: str | None = None
    config: ClientConfig | None = None
