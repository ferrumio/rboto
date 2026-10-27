use std::error::Error;
use std::fmt::{Debug, Display};

use aws_smithy_runtime_api::client::orchestrator::HttpResponse;
use aws_smithy_runtime_api::client::result::SdkError;
use aws_smithy_types::error::metadata::ProvideErrorMetadata;
use serde::Serialize;

pub type Result<T> = std::result::Result<T, CliError>;

#[derive(Debug, Serialize)]
pub struct CliError {
    pub error: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub service: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
}

impl CliError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            error: code.into(),
            message: message.into(),
            service: None,
            operation: None,
            request_id: None,
        }
    }

    pub fn input(message: impl Into<String>) -> Self {
        Self::new("InputError", message)
    }

    pub fn at(mut self, member: &str) -> Self {
        self.message = format!("{member}: {}", self.message);
        self
    }

    pub fn context(mut self, service: &str, operation: &str) -> Self {
        self.service = Some(service.into());
        self.operation = Some(operation.into());
        self
    }
}

impl Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.error, self.message)
    }
}
impl Error for CliError {}
impl From<std::io::Error> for CliError {
    fn from(error: std::io::Error) -> Self {
        Self::new(
            if error.kind() == std::io::ErrorKind::BrokenPipe {
                "BrokenPipe"
            } else {
                "IOError"
            },
            error.to_string(),
        )
    }
}
impl From<serde_json::Error> for CliError {
    fn from(error: serde_json::Error) -> Self {
        Self::input(error.to_string())
    }
}

pub fn api_error<E: ProvideErrorMetadata + Error + 'static>(
    error: SdkError<E, HttpResponse>,
    service: &str,
    operation: &str,
) -> CliError {
    let code = error
        .as_service_error()
        .and_then(|e| e.code())
        .map(str::to_owned);
    let message = error
        .as_service_error()
        .and_then(|e| e.message())
        .map(str::to_owned);
    let request_id = error.raw_response().and_then(|response| {
        response
            .headers()
            .get("x-amzn-requestid")
            .or_else(|| response.headers().get("x-amz-request-id"))
            .map(str::to_owned)
    });
    let mut result = stream_error(error, service, operation, code.as_deref());
    if let Some(message) = message {
        result.message = message;
    }
    result.request_id = request_id;
    result
}

pub fn stream_error<E: Error + 'static, R: Debug>(
    error: SdkError<E, R>,
    service: &str,
    operation: &str,
    code: Option<&str>,
) -> CliError {
    let category = match &error {
        SdkError::TimeoutError(_) => "TimeoutError",
        SdkError::ConstructionFailure(_) => "ConstructionError",
        SdkError::DispatchFailure(_) => "DispatchError",
        SdkError::ResponseError(_) => "ResponseError",
        _ => "ServiceError",
    };
    // Display/source chains avoid dumping request bodies or signing headers.
    let mut messages = vec![error.to_string()];
    let mut source = error.source();
    while let Some(cause) = source {
        messages.push(cause.to_string());
        source = cause.source();
    }
    CliError::new(code.unwrap_or(category), messages.join(": ")).context(service, operation)
}
