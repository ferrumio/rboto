"""Internal iteration shared by generated, typed service paginators."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence, Sized
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast, runtime_checkable

_T = TypeVar("_T")
Path = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class PaginationSpec:
    input_tokens: tuple[str, ...]
    output_tokens: tuple[tuple[Path, ...], ...]
    more_results: Path | None = None


@runtime_checkable
class _NativeCursor(Protocol):
    def to_dict(self) -> dict[str, object]: ...


def _snapshot(value: object) -> object:
    # Copy only continuation state. Yielded pages retain their native ownership
    # and can be modified by the consumer without changing the next request.
    if isinstance(value, _NativeCursor):
        return _snapshot(value.to_dict())
    if isinstance(value, Mapping):
        return {key: _snapshot(item) for key, item in cast(Mapping[object, object], value).items()}
    if isinstance(value, (list, tuple)):
        return [_snapshot(item) for item in cast(Sequence[object], value)]
    return deepcopy(value)


def _present(value: object) -> bool:
    return value is not None and not (
        isinstance(value, (str, bytes, Mapping, Sequence)) and len(cast(Sized, value)) == 0
    )


def _read(value: object, path: Path) -> object:
    for part in path:
        if value is None:
            return None
        if isinstance(part, int):
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                return None
            try:
                value = cast(Sequence[object], value)[part]
            except IndexError:
                return None
        elif isinstance(value, Mapping):
            value = cast(Mapping[str, object], value).get(part)
        else:
            value = getattr(value, part, None)
    return value


def _token(page: object, alternatives: tuple[Path, ...]) -> object:
    for path in alternatives:
        value = _read(page, path)
        if _present(value):
            return value
    return None


async def paginate(
    call: Callable[..., Awaitable[_T]],
    params: dict[str, object],
    spec: PaginationSpec,
) -> AsyncIterator[_T]:
    # Generated signatures validate the public arguments. Model-checked token
    # paths are the only arguments this loop updates across requests.
    request = params.copy()
    previous = tuple(_snapshot(request.get(name)) for name in spec.input_tokens)
    while True:
        page = await call(**request)
        following = tuple(_snapshot(_token(page, paths)) for paths in spec.output_tokens)
        has_more = spec.more_results is None or bool(_read(page, spec.more_results))
        finished = not has_more or not any(_present(token) for token in following)
        repeated = following == previous
        yield page
        if finished or repeated:
            return
        for name, token in zip(spec.input_tokens, following, strict=True):
            if _present(token):
                request[name] = token
            else:
                request.pop(name, None)
        previous = tuple(_snapshot(token) for token in following)
