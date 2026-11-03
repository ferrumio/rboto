"""Boundary cases for input hints that are shared by all generated services."""

from __future__ import annotations

import subprocess
import sys
from typing import NotRequired, Required, TypedDict

import pytest
from rboto.testing import InvalidFixtureError, _copy, _validate

type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]


class Record(TypedDict, total=False):
    name: Required[str]
    extra: NotRequired[JSON]


def test_required_future_annotations_and_recursive_json() -> None:
    value = {"name": "test", "extra": {"items": [1, None, {"flag": True}]}}
    assert _validate(value, Record, "request") == value
    with pytest.raises(InvalidFixtureError, match="required"):
        _validate({"extra": {}}, Record, "request")
    with pytest.raises(InvalidFixtureError, match="unknown"):
        _validate({"name": "test", "private-key": "private-value"}, Record, "request")
    with pytest.raises(InvalidFixtureError):
        _validate({"name": "test", "extra": {1: "invalid"}}, Record, "request")


def test_recursive_containers_and_unconsumable_iterators_are_rejected() -> None:
    value: dict[str, object] = {}
    value["cycle"] = value
    with pytest.raises(InvalidFixtureError, match="acyclic"):
        _copy(value)
    with pytest.raises(InvalidFixtureError):
        _validate(value, JSON, "request")
    with pytest.raises(InvalidFixtureError, match="fixtures require"):
        _copy(iter([1, 2]))


def test_normal_import_does_not_load_testing() -> None:
    result = subprocess.run([
        sys.executable, "-c", "import sys; import rboto; assert 'rboto.testing' not in sys.modules",
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
