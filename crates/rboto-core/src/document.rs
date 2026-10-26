use std::collections::HashMap;

use aws_smithy_types::{Document, Number};
use pyo3::conversion::IntoPyObjectExt;
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString};

pub fn document_from_py(value: &Bound<'_, PyAny>) -> PyResult<Document> {
    from_py(value, 0)
}

fn from_py(value: &Bound<'_, PyAny>, depth: usize) -> PyResult<Document> {
    if depth > 128 {
        return Err(PyValueError::new_err("document exceeds 128 nesting levels"));
    }
    if value.is_none() {
        Ok(Document::Null)
    } else if value.is_instance_of::<PyBool>() {
        Ok(Document::Bool(value.extract()?))
    } else if value.is_instance_of::<PyInt>() {
        if let Ok(number) = value.extract::<i64>() {
            Ok(Document::Number(Number::NegInt(number)))
        } else {
            Ok(Document::Number(Number::PosInt(value.extract()?)))
        }
    } else if value.is_instance_of::<PyFloat>() {
        let number: f64 = value.extract()?;
        if !number.is_finite() {
            return Err(PyValueError::new_err("document numbers must be finite"));
        }
        Ok(Document::Number(Number::Float(number)))
    } else if value.is_instance_of::<PyString>() {
        Ok(Document::String(value.extract()?))
    } else if let Ok(values) = value.cast::<PyList>() {
        values
            .iter()
            .map(|item| from_py(&item, depth + 1))
            .collect::<PyResult<Vec<_>>>()
            .map(Document::Array)
    } else if let Ok(values) = value.cast::<PyDict>() {
        values
            .iter()
            .map(|(key, item)| Ok((key.extract::<String>()?, from_py(&item, depth + 1)?)))
            .collect::<PyResult<HashMap<_, _>>>()
            .map(Document::Object)
    } else {
        Err(PyTypeError::new_err(
            "document requires JSON values: null, bool, int, float, str, list, or dict[str, ...]",
        ))
    }
}

pub fn document_to_py(py: Python<'_>, value: &Document) -> PyResult<Py<PyAny>> {
    match value {
        Document::Null => Ok(py.None()),
        Document::Bool(value) => value.into_py_any(py),
        Document::String(value) => value.into_py_any(py),
        Document::Number(Number::PosInt(value)) => value.into_py_any(py),
        Document::Number(Number::NegInt(value)) => value.into_py_any(py),
        Document::Number(Number::Float(value)) => value.into_py_any(py),
        Document::Array(values) => {
            let list = PyList::empty(py);
            for value in values {
                list.append(document_to_py(py, value)?)?;
            }
            Ok(list.into_any().unbind())
        }
        Document::Object(values) => {
            let dict = PyDict::new(py);
            for (key, value) in values {
                dict.set_item(key, document_to_py(py, value)?)?;
            }
            Ok(dict.into_any().unbind())
        }
    }
}
