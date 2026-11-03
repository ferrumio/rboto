use std::future::Future;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3_async_runtimes::tokio::{
    future_into_py_with_locals, get_current_locals, init_with_runtime,
};
use tokio::runtime::{Builder, Runtime};

// These statics are local to each extension. Only the integer policy crosses
// wheels through the Python module; Rust runtimes and pointers never do.
static RUNTIME: PyOnceLock<Runtime> = PyOnceLock::new();
static REGISTERED: PyOnceLock<()> = PyOnceLock::new();

fn ensure_runtime(py: Python<'_>) -> PyResult<()> {
    REGISTERED.get_or_try_init(py, || {
        let runtime = RUNTIME.get_or_try_init(py, || {
            let available = std::thread::available_parallelism().map_or(1, usize::from);
            let workers: usize = py
                .import("rboto.runtime")?
                .getattr("_freeze_worker_threads")?
                .call1((available,))?
                .extract()?;
            // Building the pool and waiting for concurrent initialization must
            // not hold the GIL. PyOnceLock also detaches before waiting.
            py.detach(|| {
                Builder::new_multi_thread()
                    .worker_threads(workers)
                    .thread_name("rboto-worker")
                    .enable_all()
                    .build()
            })
            .map_err(|error| {
                PyRuntimeError::new_err(format!("Unable to start rboto runtime: {error}"))
            })
        })?;
        init_with_runtime(runtime).map_err(|()| {
            PyRuntimeError::new_err("rboto runtime was initialized outside its policy")
        })
    })?;
    Ok(())
}

/// Preserve the existing future bridge, cancellation and task-local context.
pub fn future_into_py<F, T>(py: Python<'_>, future: F) -> PyResult<Bound<'_, PyAny>>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: for<'py> IntoPyObject<'py> + Send + 'static,
{
    // An invalid call outside an event loop must not start or freeze a runtime.
    let locals = get_current_locals(py)?;
    ensure_runtime(py)?;
    future_into_py_with_locals(py, locals, future)
}

/// Private diagnostics for compiled contract tests; never initializes a runtime.
#[pyfunction(name = "_runtime_worker_threads")]
pub fn runtime_worker_threads(py: Python<'_>) -> Option<usize> {
    RUNTIME
        .get(py)
        .map(|runtime| runtime.metrics().num_workers())
}
