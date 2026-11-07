use futures_util::future::ready;
use futures_util::{Stream, StreamExt};
use pyo3::prelude::*;

use crate::async_iter::python_async_iterator;

/// Poll one Python event per SDK request, preserving the originating event loop.
/// There is no detached producer task or unbounded event buffer. The stream yields a
/// conversion failure once and then ends.
pub fn python_event_stream<T, E>(
    value: &Bound<'_, PyAny>,
    convert: fn(&Bound<'_, PyAny>) -> PyResult<T>,
    map_error: fn(PyErr) -> E,
) -> PyResult<impl Stream<Item = Result<T, E>> + Send + Sync + 'static>
where
    T: Send + Sync + 'static,
    E: Send + Sync + 'static,
{
    Ok(python_async_iterator(value)?
        .map(move |event| {
            event
                .and_then(|event| Python::attach(|py| convert(event.bind(py))))
                .map_err(map_error)
        })
        .scan(false, |failed, item| {
            if *failed {
                return ready(None);
            }
            *failed = item.is_err();
            ready(Some(item))
        }))
}
