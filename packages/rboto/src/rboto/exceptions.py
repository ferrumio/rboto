from typing import NotRequired, TypedDict


class ErrorDetails(TypedDict):
    Code: str
    Message: str


class ErrorResponse(TypedDict):
    Error: ErrorDetails
    RequestId: NotRequired[str]


class RbotoError(Exception):
    """Base exception for all rboto errors."""


class ClientError(RbotoError):
    """Final error returned by an AWS client operation."""

    message: str
    error_code: str
    request_id: str | None
    operation_name: str
    response: ErrorResponse

    def __init__(
        self,
        message: str,
        error_code: str = "UnknownError",
        request_id: str | None = None,
        operation_name: str = "UnknownOperation",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.request_id = request_id
        self.operation_name = operation_name
        self.response = {
            "Error": {"Code": error_code, "Message": message},
        }
        if request_id is not None:
            self.response["RequestId"] = request_id

    def __str__(self) -> str:
        return (
            f"An error occurred ({self.error_code}) when calling "
            f"{self.operation_name}: {self.message}"
        )


class ServiceError(ClientError):
    """Base exception for service-specific AWS errors."""


class ConfigurationError(RbotoError):
    """Invalid rboto configuration."""


class CredentialsError(RbotoError):
    """AWS credentials could not be loaded or refreshed."""
