//! Failure-only paths: successful conversion does not construct diagnostic state.
use pyo3::exceptions::{PyOverflowError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString};

const DETAIL: &str = "_rboto_input_detail";
const PATH_LIMIT: usize = 512;
type Detail = (String, String, String, bool);

#[derive(Clone, Copy)]
pub enum InputPath {
    Root,
    Field(&'static str),
    Index(usize),
    MapKey(usize),
    MapValue(usize),
}

impl InputPath {
    fn prefix(self) -> String {
        match self {
            Self::Root => String::new(),
            Self::Field(name) => name.to_owned(),
            Self::Index(index) => format!("[{index}]"),
            Self::MapKey(index) => format!("[key#{index}]"),
            Self::MapValue(index) => format!("[value#{index}]"),
        }
    }
}

fn bounded(value: &str, limit: usize) -> (String, bool) {
    if value.chars().count() <= limit {
        (value.to_owned(), false)
    } else {
        (
            value.chars().take(limit - 3).collect::<String>() + "...",
            true,
        )
    }
}

/// Add one schema/location segment while unwinding. Never formats a Python value.
#[cold]
pub fn input_error(error: PyErr, path: InputPath, expected: &'static str) -> PyErr {
    input_rule(error, path, expected, None)
}

#[cold]
pub fn input_rule(
    error: PyErr,
    path: InputPath,
    expected: &'static str,
    category: Option<&'static str>,
) -> PyErr {
    Python::attach(|py| {
        let value = error.value(py);
        let detail = value
            .getattr(DETAIL)
            .and_then(|item| item.extract::<Detail>());
        let (suffix, expected, category, truncated) = detail.unwrap_or_else(|_| {
            let category = category.unwrap_or_else(|| {
                if error.is_instance_of::<PyOverflowError>(py) {
                    "overflow"
                } else if error.is_instance_of::<PyTypeError>(py) {
                    "type"
                } else {
                    "value"
                }
            });
            let (expected, truncated) = bounded(expected, 160);
            (String::new(), expected, category.to_owned(), truncated)
        });
        let prefix = path.prefix();
        let joined = if prefix.is_empty() {
            suffix
        } else if suffix.is_empty() {
            prefix
        } else if suffix.starts_with('[') {
            prefix + &suffix
        } else {
            prefix + "." + &suffix
        };
        let (path, shortened) = bounded(&joined, PATH_LIMIT);
        // A custom exception may reject extra attributes. Preserve that error.
        let _ = value.setattr(DETAIL, (path, expected, category, truncated || shortened));
    });
    error
}

/// Publish the typed context only at the operation boundary.
#[cold]
pub fn input_operation(error: PyErr, service: &'static str, operation: &'static str) -> PyErr {
    Python::attach(|py| {
        let value = error.value(py);
        if let Ok((path, expected, category, truncated)) = value
            .getattr(DETAIL)
            .and_then(|item| item.extract::<Detail>())
        {
            let _ = py
                .import("rboto._conversion")
                .and_then(|module| module.getattr("attach_conversion_context"))
                .and_then(|attach| {
                    attach.call1((
                        value, service, operation, path, expected, category, truncated,
                    ))
                });
            let _ = value.delattr(DETAIL);
        }
    });
    error
}

/// Check only structure keys. The generator supplies sorted schema field names.
/// Successful checks borrow Python strings and do not build diagnostic state.
pub fn validate_input_fields(values: &Bound<'_, PyDict>, allowed: &[&str]) -> PyResult<()> {
    for (index, (key, _)) in values.iter().enumerate() {
        let key = key.cast::<PyString>().map_err(|_| {
            input_error(
                PyTypeError::new_err("structure field names must be strings"),
                InputPath::MapKey(index),
                "str (structure field)",
            )
        })?;
        // An unpaired surrogate cannot name a generated field. Do not expose
        // the Unicode conversion error, which could contain the original key.
        if !key
            .to_str()
            .is_ok_and(|name| allowed.binary_search(&name).is_ok())
        {
            return Err(input_rule(
                PyTypeError::new_err("unknown structure field"),
                InputPath::MapKey(index),
                "known structure field",
                Some("unknown_field"),
            ));
        }
    }
    Ok(())
}

/// The SDK builder has already rejected this structure. Inspect only schema
/// fields to locate a missing member, preserving the existing ValueError class.
#[cold]
pub fn input_builder_error(
    values: &Bound<'_, PyDict>,
    required: &[(&'static str, &'static str)],
) -> PyErr {
    for (key, public_name) in required {
        if let Ok(value) = values.get_item(key)
            && value.is_none_or(|value| value.is_none())
        {
            return input_rule(
                PyValueError::new_err("missing required member"),
                InputPath::Field(public_name),
                "required member",
                Some("missing"),
            );
        }
    }
    input_error(
        PyValueError::new_err("invalid structure"),
        InputPath::Root,
        "valid structure",
    )
}
