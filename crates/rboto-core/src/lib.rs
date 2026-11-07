use pyo3::prelude::*;
use pyo3::types::PyDict;

mod async_iter;
mod byte_stream;
mod config;
mod document;
mod error;
mod event_stream;
mod input_error;
mod lifecycle;
mod observation;
mod runtime;
mod stream;
mod transport;

pub use byte_stream::python_byte_stream;
pub use config::RequestConfig;
pub use document::{document_from_py, document_to_py};
pub use error::{
    ResponseMetadata, StreamErrorContext, http_sdk_error_to_py, raise_core_error, sdk_error_to_py,
    stream_error_to_py,
};
pub use event_stream::python_event_stream;
pub use input_error::{InputPath, input_builder_error, input_error, input_operation, input_rule};
pub use lifecycle::{
    ManagedResource, client_closed, client_future_into_py, ready_future, stream_closed,
};
pub use observation::OperationObservation;
pub use runtime::{future_into_py, runtime_worker_threads};
pub use stream::collected_body_to_bytes;

#[allow(clippy::too_many_arguments)]
pub fn raise_service_error(
    py: Python<'_>,
    exception_module: &str,
    service_base: &str,
    error_code: &str,
    message: &str,
    operation_name: &str,
    request_id: Option<&str>,
    metadata: &ResponseMetadata,
) -> PyErr {
    let class_name = normalize_error_class(error_code);
    let exception_class = py
        .import(exception_module)
        .and_then(|module| {
            module
                .getattr(class_name.as_str())
                .or_else(|_| module.getattr(service_base))
        })
        .or_else(|_| py.import("rboto.exceptions")?.getattr("ClientError"));

    let Ok(exception_class) = exception_class else {
        return pyo3::exceptions::PyRuntimeError::new_err(message.to_owned());
    };

    let kwargs = PyDict::new(py);
    if kwargs.set_item("message", message).is_err()
        || kwargs.set_item("error_code", error_code).is_err()
        || kwargs.set_item("operation_name", operation_name).is_err()
        || kwargs.set_item("request_id", request_id).is_err()
        || metadata
            .to_py(py)
            .and_then(|value| kwargs.set_item("response_metadata", value))
            .is_err()
    {
        return pyo3::exceptions::PyRuntimeError::new_err(message.to_owned());
    }

    match exception_class.call((), Some(&kwargs)) {
        Ok(instance) => PyErr::from_value(instance),
        Err(_) => pyo3::exceptions::PyRuntimeError::new_err(message.to_owned()),
    }
}

fn normalize_error_class(error_code: &str) -> String {
    // Match Python generation for names such as ModelStreamErrorException.
    let stem = error_code.strip_suffix("Exception").unwrap_or(error_code);
    let stem = stem.strip_suffix("Error").unwrap_or(stem);
    format!("{stem}Error")
}

#[cfg(test)]
mod tests {
    use super::normalize_error_class;

    #[test]
    fn normalizes_aws_error_codes() {
        assert_eq!(normalize_error_class("NoSuchKey"), "NoSuchKeyError");
        assert_eq!(
            normalize_error_class("ResourceNotFoundException"),
            "ResourceNotFoundError"
        );
        assert_eq!(normalize_error_class("InternalError"), "InternalError");
        assert_eq!(normalize_error_class("ModelErrorException"), "ModelError");
        assert_eq!(
            normalize_error_class("ModelStreamErrorException"),
            "ModelStreamError"
        );
    }
}
