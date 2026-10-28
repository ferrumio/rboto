from dataclasses import dataclass

from .interceptors import Interceptor, resolve_interceptors


@dataclass(frozen=True, slots=True)
class Session:
    """Configuration shared when constructing rboto service clients."""

    region: str | None = None
    profile: str | None = None
    endpoint_url: str | None = None
    interceptors: tuple[Interceptor, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "interceptors", resolve_interceptors(self.interceptors))


def resolve_config(
    session: Session | None,
    region: str | None,
    profile: str | None,
    endpoint_url: str | None,
) -> tuple[str | None, str | None, str | None]:
    if session is None:
        return region, profile, endpoint_url
    if any(value is not None for value in (region, profile, endpoint_url)):
        raise ValueError("session cannot be combined with explicit service configuration")
    return session.region, session.profile, session.endpoint_url
