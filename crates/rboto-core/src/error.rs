use std::error::Error;
use std::fmt::Debug;

use aws_credential_types::provider::error::{CredentialsError, TokenError};
use aws_smithy_runtime_api::client::orchestrator::HttpResponse;
use aws_smithy_runtime_api::client::result::{ConnectorError, SdkError};
use aws_smithy_types::error::metadata::ProvideErrorMetadata;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::raise_service_error;

/// Owned, bounded context. Never retains an HTTP response, body, or arbitrary headers.
#[derive(Clone, Debug, Default)]
pub struct ResponseMetadata {
    pub status_code: Option<u16>,
    pub request_id: Option<String>,
    pub extended_request_id: Option<String>,
    pub retry_attempts: Option<usize>,
}

fn safe_id(value: &str) -> Option<String> {
    // Drop malformed/oversized identifiers instead of logging arbitrary header data.
    (!value.is_empty() && value.len() <= 1024 && value.bytes().all(|c| c.is_ascii_graphic()))
        .then(|| value.to_owned())
}

impl ResponseMetadata {
    pub fn from_response(response: &HttpResponse) -> Self {
        Self {
            status_code: Some(response.status().as_u16()),
            request_id: ["x-amzn-requestid", "x-amz-request-id", "x-amzn-request-id"]
                .into_iter()
                .find_map(|header| response.headers().get(header).and_then(safe_id)),
            extended_request_id: response.headers().get("x-amz-id-2").and_then(safe_id),
            retry_attempts: None,
        }
    }

    pub fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let value = PyDict::new(py);
        value.set_item("HTTPStatusCode", self.status_code)?;
        value.set_item("RequestId", &self.request_id)?;
        value.set_item("HostId", &self.extended_request_id)?;
        value.set_item("RetryAttempts", self.retry_attempts)?;
        Ok(value)
    }
}

#[derive(Clone, Debug)]
pub struct StreamErrorContext {
    pub operation: &'static str,
    pub metadata: ResponseMetadata,
}

/// Walk typed causes, never diagnostic text. Also used for event receiver dispatch failures.
fn causes<'a>(error: &'a (dyn Error + 'static)) -> impl Iterator<Item = &'a (dyn Error + 'static)> {
    std::iter::successors(Some(error), |error| (*error).source()).take(64)
}

fn is_timeout(error: &(dyn Error + 'static)) -> bool {
    causes(error).any(|cause| {
        cause.is::<aws_smithy_async::future::timeout::TimedOutError>()
            || cause.is::<tokio::time::error::Elapsed>()
            || cause
                .downcast_ref::<std::io::Error>()
                .is_some_and(|error| error.kind() == std::io::ErrorKind::TimedOut)
            || cause
                .downcast_ref::<ConnectorError>()
                .is_some_and(ConnectorError::is_timeout)
            || cause
                .downcast_ref::<hyper::Error>()
                .is_some_and(hyper::Error::is_timeout)
    })
}

fn stream_failure_class(error: &(dyn Error + 'static)) -> &'static str {
    if is_timeout(error) {
        return "TimeoutError";
    }
    // Unknown/opaque body errors (including checksum validation) are response failures.
    // Invalid HTTP syntax is a decoding failure; an incomplete transport is a connection failure.
    if causes(error).any(|cause| {
        cause.downcast_ref::<std::io::Error>().is_some_and(|error| {
            matches!(
                error.kind(),
                std::io::ErrorKind::ConnectionReset
                    | std::io::ErrorKind::ConnectionAborted
                    | std::io::ErrorKind::BrokenPipe
                    | std::io::ErrorKind::UnexpectedEof
                    | std::io::ErrorKind::NotConnected
                    | std::io::ErrorKind::ConnectionRefused
            )
        }) || cause
            .downcast_ref::<hyper::Error>()
            .is_some_and(|error| !error.is_parse())
            || cause
                .downcast_ref::<ConnectorError>()
                .is_some_and(ConnectorError::is_io)
    }) {
        "ConnectionError"
    } else {
        "ResponseError"
    }
}

pub fn stream_error_to_py(
    py: Python<'_>,
    error: &(dyn Error + 'static),
    context: &StreamErrorContext,
) -> PyErr {
    if let Some(original) = causes(error).find_map(|cause| cause.downcast_ref::<PyErr>()) {
        return original.clone_ref(py);
    }
    let class = stream_failure_class(error);
    let message = match class {
        "TimeoutError" => "Response stream timed out",
        "ConnectionError" => "Connection failed while reading the response stream",
        _ => "Could not decode or validate the response stream",
    };
    raise_service_error(
        py,
        "rboto.exceptions",
        class,
        class,
        message,
        context.operation,
        context.metadata.request_id.as_deref(),
        &context.metadata,
    )
}

pub fn raise_core_error(py: Python<'_>, class: &str, message: &str) -> PyErr {
    match py
        .import("rboto.exceptions")
        .and_then(|module| module.getattr(class))
        .and_then(|class| class.call1((message,)))
    {
        Ok(instance) => PyErr::from_value(instance),
        Err(error) => error,
    }
}

/// A Python exception raised while the SDK consumed a streamed request body surfaces as a
/// dispatch failure whose source chain carries the original `PyErr`.
fn python_error_source<E, R>(error: &SdkError<E, R>) -> Option<&PyErr>
where
    E: Error + 'static,
    R: Debug + 'static,
{
    let SdkError::DispatchFailure(failure) = error else {
        return None;
    };
    let mut source: Option<&(dyn Error + 'static)> = Some(failure.as_connector_error()?);
    while let Some(current) = source {
        if let Some(py_error) = current.downcast_ref::<PyErr>() {
            return Some(py_error);
        }
        source = current.source();
    }
    None
}

fn credentials_failure(mut error: &(dyn Error + 'static)) -> bool {
    loop {
        if error.is::<CredentialsError>() || error.is::<TokenError>() {
            return true;
        }
        match error.source() {
            Some(source) => error = source,
            None => return false,
        }
    }
}

pub fn http_sdk_error_to_py<E>(
    py: Python<'_>,
    error: &SdkError<E, HttpResponse>,
    operation: &str,
    exception_module: &str,
    service_base: &str,
    metadata: &ResponseMetadata,
) -> PyErr
where
    E: ProvideErrorMetadata + Error + 'static,
{
    let response = error.raw_response();
    let mut metadata = metadata.clone();
    if let Some(response) = response {
        let current = ResponseMetadata::from_response(response);
        metadata.status_code = current.status_code;
        metadata.request_id = current.request_id;
        metadata.extended_request_id = current.extended_request_id;
    }
    let request_id = metadata.request_id.as_deref();
    // Some protocol decoders wrap malformed successful responses as an
    // unhandled service error instead of SdkError::ResponseError. Preserve
    // modeled errors (including protocols that return error codes with 2xx).
    if response.is_some_and(|response| response.status().is_success())
        && error
            .as_service_error()
            .is_some_and(|service| service.code().is_none())
    {
        return raise_service_error(
            py,
            "rboto.exceptions",
            "ResponseError",
            "ResponseError",
            "Could not decode the service response",
            operation,
            request_id,
            &metadata,
        );
    }
    sdk_error_to_py(
        py,
        error,
        operation,
        exception_module,
        service_base,
        None,
        request_id,
        &metadata,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn sdk_error_to_py<E, R>(
    py: Python<'_>,
    error: &SdkError<E, R>,
    operation: &str,
    exception_module: &str,
    service_base: &str,
    fallback_code: Option<&str>,
    request_id: Option<&str>,
    metadata: &ResponseMetadata,
) -> PyErr
where
    E: ProvideErrorMetadata + Error + 'static,
    R: Debug + 'static,
{
    if let Some(service) = error.as_service_error() {
        let modeled_id = service.meta().extra("aws_request_id").and_then(safe_id);
        let request_id = request_id.or(modeled_id.as_deref());
        return raise_service_error(
            py,
            exception_module,
            service_base,
            service.code().or(fallback_code).unwrap_or("UnknownError"),
            service.message().unwrap_or("Service returned an error"),
            operation,
            request_id,
            metadata,
        );
    }
    if let Some(py_error) = python_error_source(error) {
        return py_error.clone_ref(py);
    }
    if credentials_failure(error) {
        return raise_core_error(
            py,
            "CredentialsError",
            &format!(
                "Credentials could not be loaded or refreshed for {operation}; check the configured credential source"
            ),
        );
    }
    let (class, message) = match error {
        SdkError::TimeoutError(_) => ("TimeoutError", "Operation timed out"),
        SdkError::DispatchFailure(error)
            if error.is_timeout()
                || error
                    .as_connector_error()
                    .is_some_and(|error| is_timeout(error)) =>
        {
            ("TimeoutError", "Connection or request timed out")
        }
        SdkError::DispatchFailure(_) => (
            "ConnectionError",
            "Could not connect to the service; check the endpoint, proxy, TLS trust and network",
        ),
        SdkError::ConstructionFailure(_) => {
            return raise_core_error(
                py,
                "ConfigurationError",
                &format!(
                    "Could not construct {operation}; check client configuration and request parameters"
                ),
            );
        }
        SdkError::ResponseError(_) if is_timeout(error) => ("TimeoutError", "Response timed out"),
        SdkError::ResponseError(_) if stream_failure_class(error) == "ConnectionError" => (
            "ConnectionError",
            "Connection failed while reading the service response",
        ),
        SdkError::ResponseError(_) => ("ResponseError", "Could not decode the service response"),
        _ => ("ClientError", "Request failed"),
    };
    // Do not format Debug/request bodies/signing headers into Python messages.
    raise_service_error(
        py,
        "rboto.exceptions",
        class,
        class,
        message,
        operation,
        request_id,
        metadata,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognizes_typed_credential_errors_without_matching_text() {
        let missing = CredentialsError::not_loaded("missing test credentials");
        assert!(credentials_failure(&missing));
        assert!(!credentials_failure(&std::io::Error::other("credentials")));
    }

    #[test]
    fn classifies_typed_stream_causes_without_matching_messages() {
        assert_eq!(
            stream_failure_class(&std::io::Error::from(std::io::ErrorKind::TimedOut)),
            "TimeoutError"
        );
        assert_eq!(
            stream_failure_class(&std::io::Error::from(std::io::ErrorKind::ConnectionReset)),
            "ConnectionError"
        );
        assert_eq!(
            stream_failure_class(&std::io::Error::other("timeout connection reset")),
            "ResponseError"
        );
        let wrapped =
            ConnectorError::io(Box::new(std::io::Error::from(std::io::ErrorKind::TimedOut)));
        assert_eq!(stream_failure_class(&wrapped), "TimeoutError");
    }

    #[test]
    fn identifiers_are_bounded_and_headers_are_allowlisted() {
        assert_eq!(safe_id("request-123"), Some("request-123".to_owned()));
        assert!(safe_id("line\nbreak").is_none());
        assert!(safe_id(&"x".repeat(1025)).is_none());
    }
}
