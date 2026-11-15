use std::future::Future;
use std::sync::Arc;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::PyDict;
use pyo3_async_runtimes::TaskLocals;
use pyo3_async_runtimes::tokio::{
    future_into_py_with_locals, get_current_locals, init_with_runtime,
};
use tokio::runtime::{Builder, Runtime};

// These statics are local to each extension. Only the integer policy crosses
// wheels through the Python module; Rust runtimes and pointers never do.
static RUNTIME: PyOnceLock<Runtime> = PyOnceLock::new();
static REGISTERED: PyOnceLock<()> = PyOnceLock::new();
static MODULES: PyOnceLock<Py<PyDict>> = PyOnceLock::new();

#[derive(Clone)]
pub(crate) enum PythonRuntime {
    Asyncio(TaskLocals),
    Trio(Arc<Py<PyAny>>),
}

impl PythonRuntime {
    pub(crate) fn current(py: Python<'_>) -> PyResult<Self> {
        let modules = MODULES.get_or_try_init(py, || {
            Ok::<_, PyErr>(
                py.import("sys")?
                    .getattr("modules")?
                    .cast_into::<PyDict>()?
                    .unbind(),
            )
        })?;
        // Detect the actual task, including Trio guest runs inside an asyncio
        // host. Importing rboto must never import an optional scheduler.
        if let Some(trio) = modules.bind(py).get_item("trio")?
            && let Ok(lowlevel) = trio.getattr("lowlevel")
            && lowlevel.call_method0("in_trio_task")?.is_truthy()?
        {
            let context = py
                .import("rboto._trio")?
                .getattr("NativeContext")?
                .call0()?;
            return Ok(Self::Trio(Arc::new(context.unbind())));
        }
        Ok(Self::Asyncio(get_current_locals(py)?))
    }
}

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
    let backend = PythonRuntime::current(py)?;
    ensure_runtime(py)?;
    match backend {
        PythonRuntime::Asyncio(locals) => future_into_py_with_locals(py, locals, future),
        PythonRuntime::Trio(context) => crate::trio::future_into_py(py, context.bind(py), future),
    }
}

/// Private diagnostics for compiled contract tests; never initializes a runtime.
#[pyfunction(name = "_runtime_worker_threads")]
pub fn runtime_worker_threads(py: Python<'_>) -> Option<usize> {
    RUNTIME
        .get(py)
        .map(|runtime| runtime.metrics().num_workers())
}
