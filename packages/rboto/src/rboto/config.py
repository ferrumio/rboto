from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

from .exceptions import ConfigurationError


def _valid_attempts(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 2**32 - 1


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientConfig:
    """Credential and request settings shared when constructing clients.

    Timeouts are in seconds. ``max_attempts`` includes the initial request.
    Explicit credentials or a profile can supply the source credentials for
    ``role_arn``. Credential values are excluded from the representation.
    """

    access_key_id: str | None = field(default=None, repr=False)
    secret_access_key: str | None = field(default=None, repr=False)
    session_token: str | None = field(default=None, repr=False)
    role_arn: str | None = None
    role_session_name: str | None = None
    external_id: str | None = field(default=None, repr=False)
    connect_timeout: float | None = None
    read_timeout: float | None = None
    operation_timeout: float | None = None
    attempt_timeout: float | None = None
    max_attempts: int | None = None
    retry_mode: Literal["standard", "adaptive"] | None = None

    def __post_init__(self) -> None:
        if (self.access_key_id is None) != (self.secret_access_key is None):
            raise ConfigurationError(
                "access_key_id and secret_access_key must be supplied together"
            )
        if self.session_token is not None and self.access_key_id is None:
            raise ConfigurationError("session_token requires explicit credentials")
        if self.role_arn is None and (
            self.role_session_name is not None or self.external_id is not None
        ):
            raise ConfigurationError("role_session_name and external_id require role_arn")
        for name in (
            "access_key_id",
            "secret_access_key",
            "session_token",
            "role_arn",
            "role_session_name",
            "external_id",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ConfigurationError(f"{name} must be a nonempty string")
        for name in ("connect_timeout", "read_timeout", "operation_timeout", "attempt_timeout"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
                or value > 2**32 - 1
                or not isfinite(value)
            ):
                raise ConfigurationError(f"{name} must be finite and positive (at most 2**32 - 1)")
        if self.max_attempts is not None and not _valid_attempts(self.max_attempts):
            raise ConfigurationError("max_attempts must be an integer between 1 and 2**32 - 1")
        if self.retry_mode not in (None, "standard", "adaptive"):
            raise ConfigurationError("retry_mode must be 'standard' or 'adaptive'")
