use std::future::Future;
use std::ops::{Deref, DerefMut};
use std::sync::{Arc, Mutex};

use futures_util::future::{Either, select};
use pyo3::prelude::*;
use tokio::sync::{Mutex as AsyncMutex, MutexGuard, Notify};

#[derive(Default)]
struct State {
    closed: bool,
    active: usize,
}

#[derive(Default)]
struct Lifecycle {
    state: Mutex<State>,
    changed: Notify,
}

impl Lifecycle {
    fn enter(self: &Arc<Self>) -> Option<Lease> {
        let mut state = self.state.lock().expect("lifecycle lock");
        if state.closed {
            return None;
        }
        state.active += 1;
        Some(Lease(self.clone()))
    }

    fn close(&self) {
        self.state.lock().expect("lifecycle lock").closed = true;
        self.changed.notify_waiters();
    }

    fn is_closed(&self) -> bool {
        self.state.lock().expect("lifecycle lock").closed
    }

    fn is_idle(&self) -> bool {
        self.state.lock().expect("lifecycle lock").active == 0
    }

    async fn wait_for(&self, predicate: impl Fn(&Self) -> bool) {
        loop {
            // Register before checking state: close/drop may run on another
            // Tokio worker or Python thread between the check and the await.
            let mut notified = std::pin::pin!(self.changed.notified());
            notified.as_mut().enable();
            if predicate(self) {
                return;
            }
            notified.await;
        }
    }
}

/// An admitted native operation. Dropping it acknowledges resource release.
pub struct Lease(Arc<Lifecycle>);

impl Lease {
    async fn run_client<F, T>(self, future: F) -> PyResult<T>
    where
        F: Future<Output = PyResult<T>>,
    {
        self.run(future)
            .await
            .unwrap_or_else(|| Err(client_closed()))
    }

    /// Drop the pending work before acknowledging closure. Python task
    /// cancellation also drops this future and its owned resource.
    pub async fn run<F: Future>(self, future: F) -> Option<F::Output> {
        let closed = std::pin::pin!(self.0.wait_for(Lifecycle::is_closed));
        let future = std::pin::pin!(future);
        match select(closed, future).await {
            Either::Left(_) => None,
            Either::Right((output, _)) => Some(output),
        }
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        let mut state = self.0.state.lock().expect("lifecycle lock");
        state.active -= 1;
        if state.active == 0 {
            self.0.changed.notify_waiters();
        }
    }
}

/// One client or stream owner, with terminal closure and cancellable work.
/// Iterator access is serialized separately so close never waits for network I/O.
pub struct ManagedResource<T> {
    value: Mutex<Option<T>>,
    lifecycle: Arc<Lifecycle>,
    access: AsyncMutex<()>,
}

/// Return an iterator's single owner if only its pending advance is cancelled.
/// Explicit resource closure prevents restoration and drops the stream instead.
pub struct IteratorValue<T> {
    value: Option<T>,
    owner: Arc<ManagedResource<T>>,
}

impl<T> IteratorValue<T> {
    pub fn discard(mut self) {
        drop(self.value.take());
    }
}

impl<T> Deref for IteratorValue<T> {
    type Target = T;

    fn deref(&self) -> &T {
        self.value.as_ref().expect("owned iterator")
    }
}

impl<T> DerefMut for IteratorValue<T> {
    fn deref_mut(&mut self) -> &mut T {
        self.value.as_mut().expect("owned iterator")
    }
}

impl<T> Drop for IteratorValue<T> {
    fn drop(&mut self) {
        if let Some(value) = self.value.take() {
            self.owner.restore(value);
        }
    }
}

impl<T> ManagedResource<T> {
    pub fn new(value: T) -> Self {
        Self {
            value: Mutex::new(Some(value)),
            lifecycle: Arc::default(),
            access: AsyncMutex::new(()),
        }
    }

    pub fn begin(&self) -> Option<Lease> {
        self.lifecycle.enter()
    }

    pub fn acquire(&self) -> Option<(T, Lease)>
    where
        T: Clone,
    {
        let lease = self.begin()?;
        let value = self.value.lock().expect("resource lock").as_ref()?.clone();
        Some((value, lease))
    }

    pub fn is_closed(&self) -> bool {
        self.lifecycle.is_closed()
    }

    pub async fn access(&self) -> MutexGuard<'_, ()> {
        self.access.lock().await
    }

    pub fn take(&self) -> Option<T> {
        self.value.lock().expect("resource lock").take()
    }

    pub fn take_for_iteration(self: &Arc<Self>) -> Option<IteratorValue<T>> {
        Some(IteratorValue {
            value: Some(self.take()?),
            owner: self.clone(),
        })
    }

    pub fn restore(&self, value: T) {
        let mut stored = self.value.lock().expect("resource lock");
        // Check under the slot lock so closure cannot remove the slot and
        // subsequently have an in-flight iterator put its resource back.
        if !self.is_closed() {
            *stored = Some(value);
        }
    }

    fn start_close(&self) {
        self.lifecycle.close();
        drop(self.take());
    }

    pub fn aclose<'py>(self: Arc<Self>, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>
    where
        T: Send + 'static,
    {
        // Publish closure even if the returned awaitable is cancelled. Drop the
        // stored SDK/stream off the GIL; active leases own their remaining work.
        py.detach(|| self.start_close());
        if self.lifecycle.is_idle() {
            // Closing an unused client must not start/freeze a Tokio runtime.
            return ready_future(py, py.None());
        }
        crate::future_into_py(py, async move {
            self.lifecycle.wait_for(Lifecycle::is_idle).await;
            Ok(())
        })
    }
}

pub fn client_closed() -> PyErr {
    Python::attach(|py| crate::raise_core_error(py, "ClientClosedError", "client is closed"))
}

/// Apply the client lifetime without changing the operation's conversion bridge.
pub fn client_future_into_py<F, T>(
    py: Python<'_>,
    lease: Lease,
    future: F,
) -> PyResult<Bound<'_, PyAny>>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: for<'py> IntoPyObject<'py> + Send + 'static,
{
    crate::future_into_py(py, lease.run_client(future))
}

pub fn stream_closed() -> PyErr {
    Python::attach(|py| crate::raise_core_error(py, "StreamClosedError", "stream is closed"))
}

/// An already-completed Python awaitable without initializing a native runtime.
pub fn ready_future(py: Python<'_>, value: Py<PyAny>) -> PyResult<Bound<'_, PyAny>> {
    let future = pyo3_async_runtimes::tokio::get_current_locals(py)?
        .event_loop(py)
        .call_method0("create_future")?;
    future.call_method1("set_result", (value,))?;
    Ok(future)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Tracked(Arc<AtomicUsize>);

    impl Drop for Tracked {
        fn drop(&mut self) {
            self.0.fetch_add(1, Ordering::SeqCst);
        }
    }

    #[test]
    fn closure_cannot_restore_or_reacquire_a_resource() {
        let dropped = Arc::new(AtomicUsize::new(0));
        let resource = ManagedResource::new(Tracked(dropped.clone()));
        let value = resource.take().unwrap();
        resource.start_close();
        resource.restore(value);
        assert!(resource.take().is_none());
        assert!(resource.begin().is_none());
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn iterator_cancellation_restores_ownership_unless_closed() {
        let dropped = Arc::new(AtomicUsize::new(0));
        let resource = Arc::new(ManagedResource::new(Tracked(dropped.clone())));
        let value = resource.take_for_iteration().unwrap();
        assert!(resource.take().is_none());
        drop(value);
        assert_eq!(dropped.load(Ordering::SeqCst), 0);
        let value = resource.take_for_iteration().unwrap();
        resource.start_close();
        drop(value);
        assert!(resource.take().is_none());
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn close_interrupts_work_and_waits_for_owners_to_drop() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime.block_on(async {
            let dropped = Arc::new(AtomicUsize::new(0));
            let resource = Arc::new(ManagedResource::new(Arc::new(Tracked(dropped.clone()))));
            let (value, lease) = resource.acquire().unwrap();
            let task = tokio::spawn(lease.run(async move {
                let _value = value;
                std::future::pending::<()>().await;
            }));
            resource.start_close();
            resource.start_close();
            resource.lifecycle.wait_for(Lifecycle::is_idle).await;
            assert_eq!(dropped.load(Ordering::SeqCst), 1);
            assert!(task.await.unwrap().is_none());
        });
    }

    #[test]
    fn cancelling_work_releases_its_owner_and_lease() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime.block_on(async {
            let dropped = Arc::new(AtomicUsize::new(0));
            let resource = ManagedResource::new(Tracked(dropped.clone()));
            let lease = resource.begin().unwrap();
            let value = resource.take().unwrap();
            let task = tokio::spawn(lease.run(async move {
                let _value = value;
                std::future::pending::<()>().await;
            }));
            task.abort();
            assert!(task.await.unwrap_err().is_cancelled());
            resource.lifecycle.wait_for(Lifecycle::is_idle).await;
            assert_eq!(dropped.load(Ordering::SeqCst), 1);
        });
    }
}
