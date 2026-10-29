from dataclasses import dataclass, field

from .config import ClientConfig
from .exceptions import ConfigurationError
from .interceptors import Interceptor, resolve_interceptors


def _validate_config(config: object) -> None:
    if config is not None and not isinstance(config, ClientConfig):
        raise ConfigurationError("config must be a ClientConfig instance")


@dataclass(frozen=True, slots=True)
class Session:
    """Configuration shared when constructing rboto service clients."""

    region: str | None = None
    profile: str | None = None
    endpoint_url: str | None = None
    config: ClientConfig | None = field(default=None, kw_only=True)
    interceptors: tuple[Interceptor, ...] = ()

    def __post_init__(self) -> None:
        _validate_config(self.config)
        object.__setattr__(self, "interceptors", resolve_interceptors(self.interceptors))


def resolve_config(
    session: Session | None,
    region: str | None,
    profile: str | None,
    endpoint_url: str | None,
    config: ClientConfig | None,
) -> tuple[str | None, str | None, str | None, ClientConfig | None]:
    _validate_config(config)
    if session is None:
        return region, profile, endpoint_url, config
    if any(value is not None for value in (region, profile, endpoint_url, config)):
        raise ValueError("session cannot be combined with explicit service configuration")
    return session.region, session.profile, session.endpoint_url, session.config
