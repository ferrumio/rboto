//! Direct Trio/Tokio delivery. No asyncio loop or additional runtime is created.

use std::future::Future;
use std::sync::Mutex;

use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;
use tokio::task::AbortHandle;

#[pyclass(frozen)]
struct Abort {
    handle: AbortHandle,
}

#[pymethods]
impl Abort {
    fn cancel(&self) {
        self.handle.abort();
    }
}

pub(crate) fn future_into_py<'py, F, T>(
    py: Python<'py>,
    context: &Bound<'py, PyAny>,
    future: F,
) -> PyResult<Bound<'py, PyAny>>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: for<'a> IntoPyObject<'a> + Send + 'static,
{
    let delivery = context.call_method0("future")?;
    let task = get_runtime().spawn(future);
    let handle = task.abort_handle();
    let binding = Py::new(py, Abort { handle }).and_then(|abort| {
        delivery.call_method1("bind_cancel", (abort,))?;
        Ok(())
    });
    if let Err(error) = binding {
        task.abort();
        return Err(error);
    }
    let sender = delivery.clone().unbind();
    get_runtime().spawn(async move {
        let result = match task.await {
            Ok(result) => result,
            Err(error) if error.is_cancelled() => return,
            Err(error) => Err(PyRuntimeError::new_err(format!(
                "rboto native task failed: {error}"
            ))),
        };
        // Like the asyncio bridge, acquire Python on a blocking worker instead
        // of blocking a Tokio I/O worker while waiting for the GIL.
        get_runtime().spawn_blocking(move || {
            Python::attach(|py| {
                let sender = sender.bind(py);
                if sender
                    .getattr("_cancelled")
                    .and_then(|value| value.is_truthy())
                    .unwrap_or(true)
                {
                    return;
                }
                let result = result.and_then(|value| value.into_py_any(py));
                let (value, error) = match result {
                    Ok(value) => (value, py.None()),
                    Err(error) => (py.None(), error.into_value(py).into_any()),
                };
                // RunFinishedError is handled inside complete(). No callback is
                // allowed to resurrect a finished run or a cancelled receiver.
                let _ = sender.call_method1("complete", (value, error));
            });
        });
    });
    Ok(delivery)
}

#[pyclass(frozen)]
struct PollResult {
    sender: Mutex<Option<oneshot::Sender<PyResult<Py<PyAny>>>>>,
}

#[pymethods]
impl PollResult {
    #[pyo3(signature = (value, error=None))]
    fn __call__(&self, value: Py<PyAny>, error: Option<Bound<'_, PyAny>>) {
        let result = match error {
            Some(error) => Err(PyErr::from_value(error)),
            None => Ok(value),
        };
        if let Some(sender) = self.sender.lock().expect("input poll result").take() {
            let _ = sender.send(result);
        }
    }
}

pub(crate) fn start_poll(
    py: Python<'_>,
    context: &Bound<'_, PyAny>,
    iterator: &Py<PyAny>,
) -> PyResult<(
    Py<PyAny>,
    impl Future<Output = PyResult<Py<PyAny>>> + Send + 'static + use<>,
)> {
    let (tx, rx) = oneshot::channel();
    let callback = Py::new(
        py,
        PollResult {
            sender: Mutex::new(Some(tx)),
        },
    )?;
    let poll = context
        .call_method1("start_poll", (iterator.bind(py), callback))?
        .unbind();
    Ok((poll, async move {
        rx.await
            .map_err(|_| PyRuntimeError::new_err("Trio input poll ended without a result"))?
    }))
}
