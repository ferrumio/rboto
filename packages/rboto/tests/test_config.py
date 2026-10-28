import dataclasses

import pytest
from rboto import ClientConfig, Session
from rboto.exceptions import ConfigurationError
from rboto.services import _resolve_config


@pytest.mark.parametrize(
    "kwargs",
    [
        {"access_key_id": "key"},
        {"secret_access_key": "secret"},
        {"session_token": "token"},
        {"role_session_name": "name"},
        {"external_id": "external"},
        {"access_key_id": "", "secret_access_key": "secret"},
        {"connect_timeout": 0},
        {"read_timeout": -1},
        {"attempt_timeout": float("nan")},
        {"operation_timeout": float("inf")},
        {"connect_timeout": 1e30},
        {"connect_timeout": 10**1000},
        {"connect_timeout": True},
        {"max_attempts": 0},
        {"max_attempts": True},
        {"retry_mode": "unknown"},
    ],
)
def test_invalid_settings_fail_before_native_client_creation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ConfigurationError):
        ClientConfig(**kwargs)  # type: ignore[arg-type]


def test_credentials_are_not_in_representations() -> None:
    config = ClientConfig(
        access_key_id="test-access",
        secret_access_key="test-secret",
        session_token="test-token",
        role_arn="arn:example",
        external_id="test-external",
    )
    text = repr(Session(config=config))
    for secret in ("test-access", "test-secret", "test-token", "test-external"):
        assert secret not in text
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(config, "max_attempts", 9)


def test_session_forwards_configuration_without_silent_overrides() -> None:
    config = ClientConfig(max_attempts=2, connect_timeout=0.25)
    session = Session(region="us-east-1", profile="example", config=config)
    assert _resolve_config(session, None, None, None, None) == (
        "us-east-1",
        "example",
        None,
        config,
    )
    with pytest.raises(ValueError, match="cannot be combined"):
        _resolve_config(session, None, None, None, config)
