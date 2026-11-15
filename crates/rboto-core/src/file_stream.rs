use std::fs::Metadata;
use std::io::{Error, ErrorKind};
use std::path::PathBuf;
use std::sync::Arc;

use aws_smithy_types::body::SdkBody;
use aws_smithy_types::byte_stream::ByteStream;
use bytes::BytesMut;
use futures_util::stream;
use pyo3::prelude::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::byte_stream::StreamBody;
use crate::{
    ManagedResource, StreamErrorContext, future_into_py, stream_closed, stream_error_to_py,
};

fn check_metadata(expected: &Metadata, actual: &Metadata) -> PyResult<()> {
    #[cfg(unix)]
    let same_identity = {
        use std::os::unix::fs::MetadataExt;
        expected.dev() == actual.dev() && expected.ino() == actual.ino()
    };
    #[cfg(not(unix))]
    let same_identity = true;
    if !actual.is_file()
        || expected.len() != actual.len()
        || expected.modified()? != actual.modified()?
        || !same_identity
    {
        return Err(Error::other("FileBody source changed during the operation").into());
    }
    Ok(())
}

struct Reader {
    path: PathBuf,
    expected: Metadata,
    file: Option<tokio::fs::File>,
    remaining: u64,
    buffer_size: usize,
}

impl Reader {
    async fn next(mut self) -> PyResult<Option<(bytes::Bytes, Self)>> {
        if self.file.is_none() {
            let file = tokio::fs::File::open(&self.path).await?;
            check_metadata(&self.expected, &file.metadata().await?)?;
            self.file = Some(file);
        }
        let file = self.file.as_mut().expect("opened file");
        if self.remaining == 0 {
            check_metadata(&self.expected, &file.metadata().await?)?;
            return Ok(None);
        }
        let size = self.remaining.min(self.buffer_size as u64) as usize;
        let mut buffer = BytesMut::with_capacity(size);
        let read = file.take(size as u64).read_buf(&mut buffer).await?;
        if read == 0 {
            return Err(
                Error::new(ErrorKind::UnexpectedEof, "FileBody source became shorter").into(),
            );
        }
        self.remaining -= read as u64;
        Ok(Some((buffer.freeze(), self)))
    }
}

pub(crate) async fn file_body(
    path: PathBuf,
    buffer_size: usize,
    declared_length: Option<u64>,
) -> PyResult<ByteStream> {
    let expected = tokio::fs::metadata(&path).await?;
    if !expected.is_file() {
        return Err(crate::input_rule(
            pyo3::exceptions::PyValueError::new_err("FileBody requires a regular file"),
            crate::InputPath::Root,
            "a regular file for FileBody",
            Some("file_type"),
        ));
    }
    if declared_length.is_some_and(|length| length != expected.len()) {
        return Err(crate::input_rule(
            pyo3::exceptions::PyValueError::new_err(
                "content_length does not match FileBody file size",
            ),
            crate::InputPath::Root,
            "content_length equal to FileBody file size",
            Some("length"),
        ));
    }
    let length = expected.len();
    Ok(ByteStream::new(SdkBody::retryable(move || {
        let reader = Reader {
            path: path.clone(),
            expected: expected.clone(),
            file: None,
            remaining: length,
            buffer_size,
        };
        SdkBody::from_body_1_x(StreamBody::new(
            stream::try_unfold(reader, Reader::next),
            Some(length),
        ))
    })))
}

/// Consume the remaining body into a new file without creating Python chunks.
pub fn write_stream_to_path<'py>(
    py: Python<'py>,
    inner: Arc<ManagedResource<ByteStream>>,
    context: Arc<StreamErrorContext>,
    path: PathBuf,
) -> PyResult<Bound<'py, PyAny>> {
    future_into_py(py, async move {
        let Some(lease) = inner.begin() else {
            return Err(stream_closed());
        };
        lease
            .run(async move {
                let (stream, file) = {
                    let _access = inner.access().await;
                    let file = tokio::fs::OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(path)
                        .await?;
                    (inner.take(), file)
                };
                let Some(mut stream) = stream else {
                    return Ok(0_u64);
                };
                let mut output = tokio::io::BufWriter::with_capacity(1024 * 1024, file);
                let mut written = 0_u64;
                while let Some(chunk) = stream.try_next().await.map_err(|error| {
                    Python::attach(|py| stream_error_to_py(py, &error, &context))
                })? {
                    output.write_all(&chunk).await?;
                    written += chunk.len() as u64;
                }
                output.flush().await?;
                Ok(written)
            })
            .await
            .unwrap_or_else(|| Err(stream_closed()))
    })
}
