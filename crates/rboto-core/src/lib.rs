use pyo3::prelude::*;
use pyo3::types::PyDict;

pub fn raise_service_error(
    py: Python<'_>,
    exception_module: &str,
    service_base: &str,
    error_code: &str,
    message: &str,
    operation_name: &str,
    request_id: Option<&str>,
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
    {
        return pyo3::exceptions::PyRuntimeError::new_err(message.to_owned());
    }

    match exception_class.call((), Some(&kwargs)) {
        Ok(instance) => PyErr::from_value(instance),
        Err(_) => pyo3::exceptions::PyRuntimeError::new_err(message.to_owned()),
    }
}

fn normalize_error_class(error_code: &str) -> String {
    let stem = error_code
        .strip_suffix("Exception")
        .or_else(|| error_code.strip_suffix("Error"))
        .unwrap_or(error_code);
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
    }
}
