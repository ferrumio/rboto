"""Configuration and bounded diagnostics shared by typed client waiters."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .exceptions import RbotoError


@dataclass(frozen=True, slots=True, kw_only=True)
class WaiterConfig:
    """Polling controls; seconds, independent of ClientConfig request retries."""

    delay: float | None = None
    max_attempts: int = 40
    timeout: float | None = None

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        for name, value in (("delay", self.delay), ("timeout", self.timeout)):
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value)
                or value < 0 or (name == "timeout" and value == 0)
            ):
                bound = "positive" if name == "timeout" else "nonnegative"
                raise ValueError(f"{name} must be finite and {bound}")


@dataclass(frozen=True, slots=True)
class WaiterAttempt:
    """Last completed poll; no response bodies or request parameters are retained."""

    number: int
    elapsed: float
    acceptor_index: int | None
    error_code: str | None
    request_id: str | None


class WaiterError(RbotoError):
    """Base for terminal waiter states and exhausted polling budgets."""

    def __init__(
        self, waiter_name: str, attempts: int, last_attempt: WaiterAttempt | None,
        reason: str,
    ) -> None:
        super().__init__(f"Waiter {waiter_name} stopped after {attempts} polls: {reason}")
        self.waiter_name = waiter_name
        self.attempts = attempts
        self.last_attempt = last_attempt
        self.reason = reason


class WaiterFailedError(WaiterError):
    """A modeled acceptor matched a terminal failure."""


class WaiterTimeoutError(WaiterError):
    """The maximum polls or overall deadline was reached."""

    def __init__(
        self, waiter_name: str, attempts: int, last_attempt: WaiterAttempt | None,
        reason: Literal["max_attempts", "deadline"],
    ) -> None:
        super().__init__(waiter_name, attempts, last_attempt, reason)
