use futures_util::{Stream, StreamExt, stream};
use pyo3::exceptions::PyStopAsyncIteration;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3_async_runtimes::{into_future_with_locals, tokio::get_current_locals};

static EVENT_POLL: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

struct PendingPoll {
    poll: Py<PyAny>,
    event_loop: Py<PyAny>,
    armed: bool,
}

impl Drop for PendingPoll {
    fn drop(&mut self) {
        if self.armed {
            Python::attach(|py| {
                // Dropping into_future_with_locals only drops its receiver.
                // Explicitly cancel the Python poll on its own event loop.
                let _ = self.poll.getattr(py, "cancel").and_then(|cancel| {
                    self.event_loop
                        .call_method1(py, "call_soon_threadsafe", (cancel,))
                });
            });
        }
    }
}

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
                let class = EVENT_POLL.get_or_try_init(py, || {
                    Ok::<_, PyErr>(py.import("rboto._streams")?.getattr("_EventPoll")?.unbind())
                })?;
                let poll = class.bind(py).call1((iterator.bind(py),))?;
                let guard = PendingPoll {
                    poll: poll.clone().unbind(),
                    event_loop: locals.event_loop(py).unbind(),
                    armed: true,
                };
                let future = into_future_with_locals(&locals, poll.call_method0("receive")?)?;
                Ok::<_, PyErr>((future, guard))
            });
            let result = match future {
                Ok((future, mut guard)) => {
                    let result = future.await;
                    guard.armed = false;
                    result
                }
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
