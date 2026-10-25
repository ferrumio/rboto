use futures_util::{Stream, StreamExt, stream};
use pyo3::exceptions::PyStopAsyncIteration;
use pyo3::prelude::*;
use pyo3_async_runtimes::{into_future_with_locals, tokio::get_current_locals};

/// Poll one Python event per SDK request, preserving the originating event loop.
/// There is no detached producer task or unbounded event buffer.
pub fn python_event_stream<T, E>(
    value: &Bound<'_, PyAny>,
    convert: fn(&Bound<'_, PyAny>) -> PyResult<T>,
    map_error: fn(PyErr) -> E,
) -> PyResult<impl Stream<Item = Result<T, E>> + Send + Sync + 'static>
where
    T: Send + Sync + 'static,
    E: Send + Sync + 'static,
{
    let locals = get_current_locals(value.py())?;
    let iterator = value.call_method0("__aiter__")?.unbind();
    Ok(stream::unfold(
        (iterator, locals, false),
        move |(iterator, locals, done)| async move {
            if done {
                return None;
            }
            let future = Python::attach(|py| {
                into_future_with_locals(&locals, iterator.bind(py).call_method0("__anext__")?)
            });
            let result = match future {
                Ok(future) => future.await,
                Err(error) => Err(error),
            };
            if let Err(error) = &result
                && Python::attach(|py| error.is_instance_of::<PyStopAsyncIteration>(py))
            {
                return None;
            }
            let converted = result.and_then(|event| Python::attach(|py| convert(event.bind(py))));
            let done = converted.is_err();
            Some((converted.map_err(map_error), (iterator, locals, done)))
        },
    )
    .fuse())
}
