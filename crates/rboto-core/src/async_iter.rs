use futures_util::{Stream, StreamExt, future::Either, stream};
use pyo3::exceptions::PyStopAsyncIteration;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3_async_runtimes::into_future_with_locals;

use crate::runtime::PythonRuntime;

static EVENT_POLL: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

struct PendingPoll {
    poll: Py<PyAny>,
    backend: PythonRuntime,
    armed: bool,
}

impl Drop for PendingPoll {
    fn drop(&mut self) {
        if self.armed {
            Python::attach(|py| {
                // Dropping into_future_with_locals only drops its receiver.
                // Explicitly cancel the Python poll on its own event loop.
                match &self.backend {
                    PythonRuntime::Asyncio(locals) => {
                        let _ = self.poll.getattr(py, "cancel").and_then(|cancel| {
                            locals
                                .event_loop(py)
                                .call_method1("call_soon_threadsafe", (cancel,))
                        });
                    }
                    PythonRuntime::Trio(_) => {
                        let _ = self.poll.call_method0(py, "cancel");
                    }
                }
            });
        }
    }
}

/// Drive a Python async iterable from Rust, one item per poll.
///
/// Each `__anext__` is awaited by a task on the event loop that owns the calling request,
/// through the `rboto._streams._EventPoll` cancellation boundary. There is no detached
/// producer task and no buffer beyond the item in flight. Because the poll runs on the
/// loop thread, native rboto iterators such as a response `ByteStream` can feed a request
/// even though the SDK polls the resulting stream on a Tokio worker. The stream ends on
/// `StopAsyncIteration` and fuses after the first error.
pub(crate) fn python_async_iterator(
    value: &Bound<'_, PyAny>,
) -> PyResult<impl Stream<Item = PyResult<Py<PyAny>>> + Send + Sync + 'static> {
    let backend = PythonRuntime::current(value.py())?;
    let iterator = value.call_method0("__aiter__")?.unbind();
    Ok(stream::unfold(
        (iterator, backend, false),
        move |(iterator, backend, done)| async move {
            if done {
                return None;
            }
            let future = Python::attach(|py| {
                let (poll, future) = match &backend {
                    PythonRuntime::Asyncio(locals) => {
                        let class = EVENT_POLL.get_or_try_init(py, || {
                            Ok::<_, PyErr>(
                                py.import("rboto._streams")?.getattr("_EventPoll")?.unbind(),
                            )
                        })?;
                        let poll = class.bind(py).call1((iterator.bind(py),))?;
                        let future =
                            into_future_with_locals(locals, poll.call_method0("receive")?)?;
                        (poll.unbind(), Either::Left(future))
                    }
                    PythonRuntime::Trio(context) => {
                        let (poll, future) =
                            crate::trio::start_poll(py, context.bind(py), &iterator)?;
                        (poll, Either::Right(future))
                    }
                };
                let guard = PendingPoll {
                    poll,
                    backend: backend.clone(),
                    armed: true,
                };
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
            let done = result.is_err();
            Some((result, (iterator, backend, done)))
        },
    )
    .fuse())
}
