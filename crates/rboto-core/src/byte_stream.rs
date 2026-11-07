use std::pin::Pin;
use std::task::{Context, Poll};

use aws_smithy_types::body::SdkBody;
use aws_smithy_types::byte_stream::ByteStream;
use bytes::Bytes;
use futures_util::{Stream, StreamExt};
use http_body::{Body, Frame, SizeHint};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyBytes};

use crate::async_iter::python_async_iterator;
use crate::{InputPath, input_rule};

/// Build a request `ByteStream` from Python request body input.
///
/// `bytes` and `bytearray` are copied once into a replayable in-memory body. Any other
/// object exposing `__aiter__` is streamed chunk by chunk without buffering; each chunk
/// must be `bytes`. A streamed body is consumed once and cannot be retried by the SDK.
///
/// `declared_length` is the exact byte length from the operation's `content_length`
/// parameter. Services such as S3 refuse streaming uploads of unknown size, so operations
/// that model `content_length` set `length_required` and fail early with `ValueError`
/// instead of surfacing an opaque SDK dispatch failure.
pub fn python_byte_stream(
    value: &Bound<'_, PyAny>,
    declared_length: Option<u64>,
    length_required: bool,
) -> PyResult<ByteStream> {
    if value.is_instance_of::<PyBytes>() || value.is_instance_of::<PyByteArray>() {
        let bytes: Vec<u8> = value.extract()?;
        return Ok(ByteStream::from(bytes));
    }
    if !value.hasattr("__aiter__")? {
        return Err(PyTypeError::new_err(
            "streaming body must be bytes, bytearray, or an async iterable of bytes",
        ));
    }
    if length_required && declared_length.is_none() {
        // Record the rule before the generated field converter adds its path.
        // Otherwise the operation boundary replaces it with the generic body type.
        return Err(input_rule(
            PyValueError::new_err("content_length is required for an async iterable body"),
            InputPath::Root,
            "async iterable with content_length",
            Some("missing"),
        ));
    }
    let chunks = python_async_iterator(value)?.map(|chunk| {
        chunk.and_then(|chunk| {
            Python::attach(|py| {
                let chunk = chunk.bind(py);
                let Ok(chunk) = chunk.cast::<PyBytes>() else {
                    return Err(PyTypeError::new_err("streaming body chunks must be bytes"));
                };
                Ok(Bytes::copy_from_slice(chunk.as_bytes()))
            })
        })
    });
    let body = StreamBody {
        chunks: Box::pin(chunks),
        remaining: declared_length,
        finished: false,
    };
    Ok(ByteStream::new(SdkBody::from_body_1_x(body)))
}

struct StreamBody<S> {
    chunks: Pin<Box<S>>,
    remaining: Option<u64>,
    finished: bool,
}

impl<S, E> Body for StreamBody<S>
where
    S: Stream<Item = Result<Bytes, E>>,
{
    type Data = Bytes;
    type Error = E;

    fn poll_frame(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Self::Data>, Self::Error>>> {
        if self.finished {
            return Poll::Ready(None);
        }
        match self.chunks.as_mut().poll_next(cx) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(None) => {
                self.finished = true;
                Poll::Ready(None)
            }
            Poll::Ready(Some(Ok(chunk))) => {
                if let Some(remaining) = &mut self.remaining {
                    *remaining = remaining.saturating_sub(chunk.len() as u64);
                }
                Poll::Ready(Some(Ok(Frame::data(chunk))))
            }
            Poll::Ready(Some(Err(error))) => {
                self.finished = true;
                Poll::Ready(Some(Err(error)))
            }
        }
    }

    fn is_end_stream(&self) -> bool {
        self.finished
    }

    fn size_hint(&self) -> SizeHint {
        if self.finished {
            return SizeHint::with_exact(0);
        }
        match self.remaining {
            Some(size) => SizeHint::with_exact(size),
            None => SizeHint::default(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::stream;
    use futures_util::task::noop_waker_ref;

    #[test]
    fn length_hint_tracks_remaining_bytes_including_empty_chunks() {
        let chunks = [b"ab".as_slice(), b"", b"cde"]
            .map(|chunk| Ok::<_, std::io::Error>(Bytes::from_static(chunk)));
        let mut body = Box::pin(StreamBody {
            chunks: Box::pin(stream::iter(chunks)),
            remaining: Some(5),
            finished: false,
        });
        let mut cx = Context::from_waker(noop_waker_ref());
        assert_eq!(body.size_hint().exact(), Some(5));
        for remaining in [3, 3, 0] {
            assert!(matches!(
                body.as_mut().poll_frame(&mut cx),
                Poll::Ready(Some(Ok(_)))
            ));
            assert_eq!(body.size_hint().exact(), Some(remaining));
        }
        assert!(matches!(
            body.as_mut().poll_frame(&mut cx),
            Poll::Ready(None)
        ));
        assert!(body.is_end_stream());
        assert_eq!(body.size_hint().exact(), Some(0));
    }

    #[test]
    fn failed_body_stops_without_polling_the_producer_again() {
        let chunks = stream::iter([Err::<Bytes, _>(std::io::Error::other("producer"))])
            .chain(stream::poll_fn(|_| panic!("polled after producer failure")));
        let mut body = Box::pin(StreamBody {
            chunks: Box::pin(chunks),
            remaining: None,
            finished: false,
        });
        let mut cx = Context::from_waker(noop_waker_ref());
        assert_eq!(body.size_hint().exact(), None);
        assert!(matches!(
            body.as_mut().poll_frame(&mut cx),
            Poll::Ready(Some(Err(_)))
        ));
        assert!(matches!(
            body.as_mut().poll_frame(&mut cx),
            Poll::Ready(None)
        ));
        assert!(body.is_end_stream());
        assert_eq!(body.size_hint().exact(), Some(0));
    }
}
