use std::error::Error;
use std::fmt::Debug;

use aws_credential_types::provider::error::{CredentialsError, TokenError};
use aws_smithy_runtime_api::client::orchestrator::HttpResponse;
use aws_smithy_runtime_api::client::result::SdkError;
use aws_smithy_types::error::metadata::ProvideErrorMetadata;
use pyo3::prelude::*;

use crate::raise_service_error;

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
) -> PyErr
where
    E: ProvideErrorMetadata + Error + 'static,
{
    let response = error.raw_response();
    let request_id = response.and_then(|response| {
        response
            .headers()
            .get("x-amzn-requestid")
            .or_else(|| response.headers().get("x-amz-request-id"))
    });
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
) -> PyErr
where
    E: ProvideErrorMetadata + Error + 'static,
    R: Debug + 'static,
{
    if let Some(service) = error.as_service_error() {
        let request_id = request_id.or_else(|| service.meta().extra("aws_request_id"));
        return raise_service_error(
            py,
            exception_module,
            service_base,
            service.code().or(fallback_code).unwrap_or("UnknownError"),
            service.message().unwrap_or("Service returned an error"),
            operation,
            request_id,
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
        SdkError::DispatchFailure(error) if error.is_timeout() => {
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
}
