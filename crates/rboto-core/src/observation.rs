//! Per-invocation metadata collection. Never invokes Python from an SDK hook.

use std::sync::{Arc, Mutex};
use std::time::Instant;

use aws_smithy_runtime_api::box_error::BoxError;
use aws_smithy_runtime_api::client::interceptors::Intercept;
use aws_smithy_runtime_api::client::interceptors::context::{
    BeforeTransmitInterceptorContextRef, FinalizerInterceptorContextRef,
};
use aws_smithy_runtime_api::client::runtime_components::RuntimeComponents;
use aws_smithy_types::config_bag::ConfigBag;
use pyo3::prelude::*;
use pyo3::types::PyDict;

const MAX_RECORDED_ATTEMPTS: usize = 32;
type Attempt = (usize, f64, Option<u16>, Option<String>);

#[derive(Debug, Default)]
struct Record {
    started: Option<Instant>,
    attempt_count: usize,
    status_code: Option<u16>,
    request_id: Option<String>,
    attempts: Vec<Attempt>,
}

#[derive(Clone, Debug, Default)]
pub struct OperationObservation {
    record: Arc<Mutex<Record>>,
}

impl OperationObservation {
    /// Copy metadata once, before delivering the operation result to Python.
    /// Cancellation can prevent publication; Python then reports unknown values.
    pub fn publish(&self, sink: Py<PyDict>, region: Option<&str>) {
        let Ok(record) = self.record.lock() else {
            return;
        };
        Python::attach(|py| {
            let sink = sink.bind(py);
            // Observability must not replace the operation result with an error.
            let _ = (|| -> PyResult<()> {
                sink.set_item("region", region)?;
                sink.set_item("attempt_count", record.attempt_count)?;
                sink.set_item("status_code", record.status_code)?;
                sink.set_item("request_id", &record.request_id)?;
                sink.set_item("attempts", &record.attempts)?;
                sink.set_item(
                    "attempts_truncated",
                    record.attempt_count > record.attempts.len(),
                )?;
                Ok(())
            })();
        });
    }
}

impl Intercept for OperationObservation {
    fn name(&self) -> &'static str {
        "RbotoOperationObservation"
    }

    fn read_before_attempt(
        &self,
        _context: &BeforeTransmitInterceptorContextRef<'_>,
        _runtime_components: &RuntimeComponents,
        _cfg: &mut ConfigBag,
    ) -> Result<(), BoxError> {
        if let Ok(mut record) = self.record.lock() {
            record.attempt_count += 1;
            record.started = Some(Instant::now());
            record.status_code = None;
            record.request_id = None;
        }
        Ok(())
    }

    fn read_after_attempt(
        &self,
        context: &FinalizerInterceptorContextRef<'_>,
        _runtime_components: &RuntimeComponents,
        _cfg: &mut ConfigBag,
    ) -> Result<(), BoxError> {
        if let Ok(mut record) = self.record.lock() {
            if let Some(response) = context.response() {
                record.status_code = Some(response.status().as_u16());
                record.request_id = ["x-amzn-requestid", "x-amz-request-id", "x-amzn-request-id"]
                    .into_iter()
                    .find_map(|header| response.headers().get(header))
                    .map(str::to_owned);
            }
            if let Some(started) = record.started.take()
                && record.attempts.len() < MAX_RECORDED_ATTEMPTS
            {
                let attempt = (
                    record.attempt_count,
                    started.elapsed().as_secs_f64(),
                    record.status_code,
                    record.request_id.clone(),
                );
                record.attempts.push(attempt);
            }
        }
        Ok(())
    }
}
