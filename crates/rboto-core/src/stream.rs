use aws_smithy_types::byte_stream::AggregatedBytes;
use bytes::Buf;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// Build Python bytes from segments without a contiguous intermediate Rust buffer.
pub fn collected_body_to_bytes(py: Python<'_>, body: AggregatedBytes) -> PyResult<Py<PyBytes>> {
    // Reserving the exact size avoids growing or consolidating a second body buffer.
    PyBytes::new_with_writer(py, body.remaining(), |writer| {
        for segment in body.into_segments() {
            writer.write_bytes(&segment)?;
        }
        Ok(())
    })
    .map(Bound::unbind)
}
