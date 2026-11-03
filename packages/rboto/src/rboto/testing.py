"""Client-scoped expectations for application tests, without native dispatch."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import inspect
import keyword
import types
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from threading import RLock
from typing import (
    Literal,
    NotRequired,
    ParamSpec,
    Protocol,
    Required,
    TypeAliasType,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

from .exceptions import ClientError, ServiceError
from .interceptors import Interceptor, Next, OperationRequest, OperationResponse

__all__ = ["InvalidFixtureError", "Stubber", "StubMismatchError", "UnconsumedStubsError"]

_P = ParamSpec("_P")
_Output = TypeVar("_Output")


class InvalidFixtureError(ValueError):
    """An expectation does not conform to the operation's generated contract."""


class StubMismatchError(AssertionError):
    """A call has no matching expectation. No native request was made."""


class UnconsumedStubsError(AssertionError):
    """The test finished without making all expected calls."""


class _Client(Protocol):
    _interceptors: tuple[Interceptor, ...]

    @staticmethod
    def _stub_operations() -> dict[str, str]: ...


def _copy(value: object, depth: int = 0) -> object:
    """Snapshot supported containers and bound recursion before entering Rust."""
    if depth > 64:
        raise InvalidFixtureError("fixtures must be acyclic and at most 64 levels deep")
    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return value
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        if not all(isinstance(key, str) for key in mapping):
            raise InvalidFixtureError("fixture mapping keys must be strings")
        return {key: _copy(item, depth + 1) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        return [_copy(item, depth + 1) for item in cast(Sequence[object], value)]
    raise InvalidFixtureError("fixtures require mappings, sequences and scalar values; "
                              "supply bytes for a response body, not an existing stream")


def _validate(value: object, annotation: object, path: str, depth: int = 0) -> object:
    """Validate generated input hints and normalize accepted sequence containers."""
    if depth > 64:
        raise InvalidFixtureError(f"{path}: recursive values exceed 64 levels")
    if isinstance(annotation, TypeAliasType):
        return _validate(value, annotation.__value__, path, depth + 1)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Required, NotRequired):
        return _validate(value, args[0], path, depth + 1)
    if origin in (types.UnionType, Union):
        for alternative in args:
            try:
                return _validate(value, alternative, path, depth + 1)
            except InvalidFixtureError:
                pass
        raise InvalidFixtureError(f"{path}: value does not match the modeled alternatives")
    if origin is Literal:
        if any(type(value) is type(item) and value == item for item in args):
            return value
        raise InvalidFixtureError(f"{path}: expected a modeled literal")
    if is_typeddict(annotation):
        if not isinstance(value, dict):
            raise InvalidFixtureError(f"{path}: expected a mapping")
        hints = get_type_hints(annotation, include_extras=True)
        mapping = cast(dict[object, object], value)
        if mapping.keys() - hints.keys():
            raise InvalidFixtureError(f"{path}: unknown modeled field")
        required = {
            name for name, hint in hints.items()
            if get_origin(hint) is Required or (
                get_origin(hint) is not NotRequired
                and name in cast(frozenset[str], getattr(annotation, "__required_keys__"))
            )
        }
        if required - mapping.keys():
            raise InvalidFixtureError(f"{path}: missing a required modeled field")
        return {
            name: _validate(mapping[name], hint, f"{path}.{name}", depth + 1)
            for name, hint in hints.items() if name in mapping
        }
    if origin in (list, Sequence):
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise InvalidFixtureError(f"{path}: expected a sequence")
        return [
            _validate(item, args[0], f"{path}[]", depth + 1)
            for item in cast(Sequence[object], value)
        ]
    if origin is dict:
        if not isinstance(value, dict):
            raise InvalidFixtureError(f"{path}: expected a mapping")
        return {
            _validate(key, args[0], f"{path}.<key>", depth + 1):
            _validate(item, args[1], f"{path}[]", depth + 1)
            for key, item in cast(dict[object, object], value).items()
        }
    if annotation is type(None) and value is None:
        return None
    if any(annotation is item for item in (str, bytes, bool, int, float)):
        expected = cast(type[object], annotation)
        if isinstance(value, expected) and (
            expected not in (int, float) or not isinstance(value, bool)
        ):
            return value
        if expected is float and type(value) is int:
            return value
        raise InvalidFixtureError(f"{path}: expected {expected.__name__}")
    raise InvalidFixtureError(f"{path}: this input type is not supported by Stubber "
                              "(event-stream inputs cannot be snapshotted)")


def _parameter_key(name: str) -> str:
    return name[:-1] if name.endswith("_") and keyword.iskeyword(name[:-1]) else name


def _client_error(error: object) -> ClientError:
    if not isinstance(error, ClientError):
        raise InvalidFixtureError("expected ClientError or a modeled service exception")
    return error


@dataclass(frozen=True)
class _Contract:
    name: str
    signature: inspect.Signature
    hints: dict[str, object]
    exception_module: str

    def parameters(self, args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, object]:
        try:
            bound = self.signature.bind(*args, **kwargs)
        except TypeError as error:
            raise InvalidFixtureError(f"{self.name}: {error}") from None
        bound.apply_defaults()
        return {
            _parameter_key(name): _validate(value, self.hints[name], f"{self.name}.{name}")
            for name, value in bound.arguments.items()
            if value is not None
            or self.signature.parameters[name].default is inspect.Parameter.empty
        }

    def actual_parameters(self, parameters: dict[str, object]) -> dict[str, object]:
        names = {_parameter_key(name): name for name in self.signature.parameters}
        if parameters.keys() - names.keys():
            raise InvalidFixtureError(f"{self.name}: unknown operation parameter")
        return self.parameters((), {names[key]: value for key, value in parameters.items()})


@dataclass(frozen=True)
class _Expectation:
    contract: _Contract
    parameters: dict[str, object]
    output: object
    error: ClientError | None = None


class Stubber:
    """Register typed calls and native responses for one client.

    Match by operation and parameters, independently of concurrent task ordering.
    Identical calls consume expectations in registration order. Enter the context
    before starting tasks, and await them before leaving it.
    """

    def __init__(self, client: _Client) -> None:
        self._client = client
        self._operations = client._stub_operations()
        self._contracts: dict[str, _Contract] = {}
        self._expectations: list[_Expectation] = []
        self._previous: tuple[Interceptor, ...] | None = None
        self._lock = RLock()

    def _contract(self, operation: object) -> _Contract:
        if not inspect.ismethod(operation) or operation.__self__ is not self._client:
            raise InvalidFixtureError("pass a bound operation from this Stubber's client")
        name = operation.__name__
        if name not in self._operations or (
            operation.__func__ is not getattr(type(self._client), name, None)
        ):
            raise InvalidFixtureError("expected a generated client operation")
        if name not in self._contracts:
            self._contracts[name] = _Contract(
                self._operations[name], inspect.signature(operation),
                get_type_hints(operation, include_extras=True),
                operation.__module__.rsplit(".", 1)[0] + ".exceptions",
            )
        return self._contracts[name]

    def add_response(
        self,
        operation: Callable[_P, Awaitable[_Output]],
        response: dict[str, object] | _Output,
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> None:
        """Validate and register a response plus the exact expected call arguments.

        Response mappings use snake_case fields. Missing nullable fields become
        None. Byte-stream fields accept bytes and create one fresh stream per
        registration. Existing native Outputs are accepted only without streams.
        """
        with self._lock:
            contract = self._contract(operation)
            parameters = contract.parameters(args, kwargs)
            output_type = contract.hints["return"]
            if output_type is type(None):
                if response is not None:
                    raise InvalidFixtureError(f"{contract.name}: requires a None response")
                output: object = None
            elif not isinstance(output_type, type) or is_typeddict(output_type):
                raise InvalidFixtureError(
                    f"{contract.name}: event-stream responses are unsupported"
                )
            elif isinstance(response, output_type):
                # This checks for streams without consuming them; native identity
                # and unknown union variants remain intact.
                to_dict = cast(Callable[[], dict[str, object]], getattr(response, "to_dict"))
                _copy(to_dict())
                output = response
            elif isinstance(response, dict):
                factory = getattr(output_type, "_from_fixture", None)
                if not callable(factory):
                    raise InvalidFixtureError("rebuild the service package to enable fixtures")
                snapshot = _copy(cast(dict[str, object], response))
                try:
                    output = factory(snapshot)
                except (TypeError, ValueError, OverflowError) as error:
                    raise InvalidFixtureError(
                        f"{contract.name}: invalid response fixture ({error})"
                    ) from None
            else:
                raise InvalidFixtureError(
                    f"{contract.name}: expected a mapping or {output_type.__name__}"
                )
            self._expectations.append(_Expectation(contract, parameters, output))

    def add_error(
        self,
        operation: Callable[_P, Awaitable[_Output]],
        error: ClientError,
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> None:
        """Register a ClientError or a modeled exception from this client's service."""
        with self._lock:
            contract = self._contract(operation)
            parameters = contract.parameters(args, kwargs)
            error = _client_error(error)
            if isinstance(error, ServiceError):
                module = import_module(contract.exception_module)
                if getattr(module, type(error).__name__, None) is not type(error):
                    raise InvalidFixtureError("modeled exception belongs to another service")
            if error.operation_name not in ("UnknownOperation", contract.name):
                raise InvalidFixtureError("exception operation_name does not match the operation")
            # Copy so repeated raises have independent tracebacks and do not
            # mutate the caller's exception.
            instance = type(error)(
                message=error.message, error_code=error.error_code,
                request_id=error.request_id, operation_name=contract.name,
            )
            self._expectations.append(_Expectation(contract, parameters, None, instance))

    def __enter__(self) -> Stubber:
        with self._lock:
            if self._previous is not None or any(
                isinstance(item, Stubber) for item in self._client._interceptors
            ):
                raise RuntimeError("this client already has an active Stubber")
            self._previous = self._client._interceptors
            self._client._interceptors = (*self._previous, self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        with self._lock:
            if self._previous is not None:
                self._client._interceptors = self._previous
                self._previous = None
            if exc_type is None:
                self.assert_no_pending_responses()

    def assert_no_pending_responses(self) -> None:
        """Assert all registered responses and errors were consumed."""
        with self._lock:
            if self._expectations:
                names = sorted({item.contract.name for item in self._expectations})
                raise UnconsumedStubsError(
                    f"{len(self._expectations)} unconsumed expectation(s): {', '.join(names)}"
                )

    async def __call__(
        self, request: OperationRequest, call_next: Next, /
    ) -> OperationResponse:
        with self._lock:
            if self._previous is None:
                raise StubMismatchError(
                    "Stubber is inactive; await tasks before exiting its context"
                )
            candidates = [
                (index, item) for index, item in enumerate(self._expectations)
                if item.contract.name == request.context.operation
            ]
            if not candidates:
                raise StubMismatchError(
                    f"{request.context.operation}: unexpected call; no expectation registered"
                )
            try:
                actual = candidates[0][1].contract.actual_parameters(request.parameters)
            except InvalidFixtureError as error:
                raise StubMismatchError(str(error)) from None
            for index, item in candidates:
                if actual == item.parameters:
                    self._expectations.pop(index)
                    if item.error is not None:
                        raise item.error
                    return OperationResponse(item.output)
            # Report only top-level modeled names, never payloads or map keys.
            changed = sorted({
                name for _, item in candidates for name in actual.keys() | item.parameters.keys()
                if name not in actual or name not in item.parameters
                or actual[name] != item.parameters[name]
            })
            raise StubMismatchError(
                f"{request.context.operation}: parameters differ from "
                f"{len(candidates)} expectation(s); check {', '.join(changed)}"
            )
