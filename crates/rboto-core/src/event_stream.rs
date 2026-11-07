use futures_util::{Stream, StreamExt, stream};
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
    Ok(stop_after_error(python_async_iterator(value)?.map(
        move |event| {
            event
                .and_then(|event| Python::attach(|py| convert(event.bind(py))))
                .map_err(map_error)
        },
    )))
}

fn stop_after_error<T, E>(
    input: impl Stream<Item = Result<T, E>>,
) -> impl Stream<Item = Result<T, E>> {
    // Check terminal state before polling upstream. StreamExt::scan checks it
    // only after consuming another item, which could run or block user code.
    stream::unfold((Box::pin(input), false), |(mut input, failed)| async move {
        if failed {
            return None;
        }
        let item = input.next().await?;
        let failed = item.is_err();
        Some((item, (input, failed)))
    })
    .fuse()
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::task::noop_waker_ref;
    use std::task::{Context, Poll};

    #[test]
    fn conversion_failure_is_terminal_without_another_producer_poll() {
        let input = stream::iter([Ok(1), Err("conversion")]).chain(stream::poll_fn(|_| {
            panic!("polled after conversion failure")
        }));
        let mut stream = Box::pin(stop_after_error(input));
        let mut cx = Context::from_waker(noop_waker_ref());
        assert_eq!(stream.as_mut().poll_next(&mut cx), Poll::Ready(Some(Ok(1))));
        assert_eq!(
            stream.as_mut().poll_next(&mut cx),
            Poll::Ready(Some(Err("conversion")))
        );
        assert_eq!(stream.as_mut().poll_next(&mut cx), Poll::Ready(None));
        assert_eq!(stream.as_mut().poll_next(&mut cx), Poll::Ready(None));
    }
}
