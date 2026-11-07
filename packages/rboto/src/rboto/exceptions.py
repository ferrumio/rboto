from collections.abc import Mapping
from typing import NotRequired, TypedDict

from ._conversion import (
    ConversionErrorCategory as ConversionErrorCategory,
)
from ._conversion import (
    ConversionErrorContext as ConversionErrorContext,
)
from ._conversion import (
    get_conversion_context as get_conversion_context,
)


class ErrorDetails(TypedDict):
    Code: str
    Message: str


class ResponseMetadata(TypedDict):
    """Allowlisted response context; unavailable values are None."""

    HTTPStatusCode: int | None
    RequestId: str | None
    HostId: str | None
    RetryAttempts: int | None


class ErrorResponse(TypedDict):
    Error: ErrorDetails
    RequestId: NotRequired[str]
    ResponseMetadata: ResponseMetadata


class RbotoError(Exception):
    """Base exception for all rboto errors."""


class ClientError(RbotoError):
    """Final error returned by an AWS client operation."""

    message: str
    error_code: str
    request_id: str | None
    operation_name: str
    response: ErrorResponse
    response_metadata: ResponseMetadata
    _details: Mapping[str, object]

    def __init__(
        self,
        message: str,
        error_code: str = "UnknownError",
        request_id: str | None = None,
        operation_name: str = "UnknownOperation",
        *,
        response_metadata: ResponseMetadata | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.response_metadata = (
            response_metadata.copy() if response_metadata is not None else {
                "HTTPStatusCode": None, "RequestId": None,
                "HostId": None, "RetryAttempts": None,
            }
        )
        self.request_id = request_id or self.response_metadata["RequestId"]
        self.response_metadata["RequestId"] = self.request_id
        self.operation_name = operation_name
        self._details = dict(details) if details is not None else {}
        self.response = {
            "Error": {"Code": error_code, "Message": message},
            "ResponseMetadata": self.response_metadata,
        }
        if self.request_id is not None:
            self.response["RequestId"] = self.request_id

    @property
    def details(self) -> Mapping[str, object]:
        """Decoded modeled fields. May contain application data; never logged automatically."""
        return self._details

    def __str__(self) -> str:
        return (
            f"An error occurred ({self.error_code}) when calling "
            f"{self.operation_name}: {self.message}"
        )


class ServiceError(ClientError):
    """Base exception for service-specific AWS errors."""


class ConfigurationError(RbotoError):
    """Invalid rboto configuration."""


class ClientClosedError(RbotoError):
    """An operation used a closed client or was interrupted by its closure."""


class StreamClosedError(RbotoError):
    """Stream closure interrupted a read, or a closed stream was entered."""


class CredentialsError(RbotoError):
    """AWS credentials could not be loaded or refreshed."""


class ConnectionError(ClientError):
    """Transport failed while dispatching an operation or consuming its response."""


class TimeoutError(ConnectionError):
    """A native connection, request or stream timeout."""


class ResponseError(ClientError):
    """The service response could not be decoded."""
