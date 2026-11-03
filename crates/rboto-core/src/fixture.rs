//! Validation used only by generated test fixture constructors.
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

pub fn fixture_fields(
    values: &Bound<'_, PyDict>,
    allowed: &[&str],
    required: &[&str],
) -> PyResult<()> {
    for key in values.keys() {
        let key: String = key.extract()?;
        if !allowed.contains(&key.as_str()) {
            return Err(PyValueError::new_err("fixture contains an unknown field"));
        }
    }
    for name in required {
        if !values.contains(name)? {
            return Err(PyValueError::new_err(format!(
                "fixture requires field '{name}'"
            )));
        }
    }
    Ok(())
}

pub fn fixture_scalar(value: &Bound<'_, PyAny>, kind: &str) -> PyResult<()> {
    let valid = match kind {
        "string" | "enum" | "datetime" => value.is_instance_of::<PyString>(),
        "bool" => value.is_instance_of::<PyBool>(),
        "i8" | "i16" | "i32" | "i64" => {
            value.is_instance_of::<PyInt>() && !value.is_instance_of::<PyBool>()
        }
        "f32" | "f64" => {
            (value.is_instance_of::<PyInt>() || value.is_instance_of::<PyFloat>())
                && !value.is_instance_of::<PyBool>()
        }
        "blob" | "byte_stream" => value.is_instance_of::<PyBytes>(),
        "vec" => value.is_instance_of::<PyList>() || value.is_instance_of::<PyTuple>(),
        _ => true,
    };
    if valid {
        Ok(())
    } else {
        Err(PyTypeError::new_err(format!(
            "invalid fixture value for {kind}"
        )))
    }
}
