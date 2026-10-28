use std::error::Error;
use std::fmt::Debug;

use aws_credential_types::provider::error::{CredentialsError, TokenError};
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
