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
        return Err(PyValueError::new_err(
            "content_length is required when the request body is an async iterable",
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
    let body = PythonBody {
        chunks: Box::pin(chunks),
        size_hint: declared_length,
        finished: false,
    };
    Ok(ByteStream::new(SdkBody::from_body_1_x(body)))
}

struct PythonBody {
    chunks: Pin<Box<dyn Stream<Item = PyResult<Bytes>> + Send + Sync + 'static>>,
    size_hint: Option<u64>,
    finished: bool,
}

impl Body for PythonBody {
    type Data = Bytes;
    type Error = PyErr;

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
            Poll::Ready(Some(Ok(chunk))) => Poll::Ready(Some(Ok(Frame::data(chunk)))),
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
        match self.size_hint {
            Some(size) => SizeHint::with_exact(size),
            None => SizeHint::default(),
        }
    }
}
