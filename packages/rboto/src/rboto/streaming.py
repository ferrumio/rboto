"""File sources for modeled streaming request bodies."""

from dataclasses import dataclass
from os import PathLike, fspath


def _path_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("FileBody.path must be a string or PathLike[str]")
    if not value or "\x00" in value:
        raise ValueError("FileBody.path must be nonempty and contain no null character")
    return value


def _validate_buffer_size(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16 * 1024 * 1024:
        raise ValueError("FileBody.buffer_size must be an integer between 1 and 16777216")


@dataclass(frozen=True, slots=True)
class FileBody:
    """Read a regular file on the native runtime, reopening it for each retry.

    Construction performs no file I/O. Keep the source unchanged until the
    operation finishes, including retries. Each invocation has an independent
    reader. ``buffer_size`` bounds each native reader's requested chunk size.
    """

    path: str | PathLike[str]
    buffer_size: int = 1024 * 1024

    def __post_init__(self) -> None:
        _validate_buffer_size(self.buffer_size)
        object.__setattr__(self, "path", _path_string(fspath(self.path)))
