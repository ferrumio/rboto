from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from rboto.streaming import FileBody


def test_descriptor_does_not_open_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "not-created"
    body = FileBody(path)
    assert body.path == str(path)
    assert not path.exists()
    with pytest.raises(FrozenInstanceError):
        setattr(body, "path", "changed")


@pytest.mark.parametrize("size", [True, 0, -1, 16 * 1024 * 1024 + 1, 1.5, "1024"])
def test_invalid_buffer_size(size: Any) -> None:
    with pytest.raises(ValueError, match="buffer_size"):
        FileBody("file", buffer_size=size)


@pytest.mark.parametrize("path", ["", "a\x00b"])
def test_invalid_string_path(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        FileBody(path)


def test_byte_path_is_not_silently_decoded() -> None:
    path: Any = b"file"
    with pytest.raises(TypeError, match="PathLike"):
        FileBody(path)
