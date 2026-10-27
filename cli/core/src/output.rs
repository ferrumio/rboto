use aws_smithy_types::byte_stream::ByteStream;
use base64::Engine;
use serde_json::{Value, json};
use std::path::Path;
use tokio::io::{AsyncWrite, AsyncWriteExt};

use crate::{CliError, Options, Result};

pub fn bytes_json(bytes: &[u8]) -> Value {
    Value::String(base64::engine::general_purpose::STANDARD.encode(bytes))
}

pub struct Output {
    pub options: Options,
}

impl Output {
    pub fn new(options: Options) -> Self {
        Self { options }
    }

    pub async fn emit(&self, value: &Value) -> Result<()> {
        let mut bytes = if self.options.pretty {
            serde_json::to_vec_pretty(value)?
        } else {
            serde_json::to_vec(value)?
        };
        bytes.push(b'\n');
        if self.options.output_file.as_deref() == Some("-") {
            let mut output = tokio::io::stderr();
            output.write_all(&bytes).await?;
            output.flush().await?;
        } else {
            let mut output = tokio::io::stdout();
            output.write_all(&bytes).await?;
            output.flush().await?;
        }
        Ok(())
    }

    pub async fn bytes(&self, member: &str, bytes: &[u8]) -> Result<Value> {
        if !self.options.selects(member) {
            return Ok(bytes_json(bytes));
        }
        let mut destination =
            Destination::new(self.options.output_file.as_deref().unwrap()).await?;
        destination.writer.write_all(bytes).await?;
        destination.finish(bytes.len() as u64).await
    }

    pub async fn stream(&self, member: &str, mut stream: ByteStream) -> Result<Value> {
        if !self.options.selects(member) {
            return Err(CliError::input(format!(
                "select {member} with --output-member and --output-file"
            )));
        }
        let mut destination =
            Destination::new(self.options.output_file.as_deref().unwrap()).await?;
        let mut size = 0;
        while let Some(bytes) = stream.next().await {
            let bytes = bytes.map_err(|error| CliError::new("StreamError", error.to_string()))?;
            destination.writer.write_all(&bytes).await?;
            size += bytes.len() as u64;
        }
        destination.finish(size).await
    }
}

struct Destination {
    writer: Box<dyn AsyncWrite + Unpin + Send>,
    temporary: Option<tempfile::NamedTempFile>,
    path: String,
}

impl Destination {
    async fn new(path: &str) -> Result<Self> {
        if path == "-" {
            return Ok(Self {
                writer: Box::new(tokio::io::stdout()),
                temporary: None,
                path: path.into(),
            });
        }
        let parent = Path::new(path)
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let temporary = tempfile::Builder::new()
            .prefix(".rboto-")
            .tempfile_in(parent)?;
        let writer = tokio::fs::File::from_std(temporary.reopen()?);
        Ok(Self {
            writer: Box::new(writer),
            temporary: Some(temporary),
            path: path.into(),
        })
    }

    async fn finish(mut self, size: u64) -> Result<Value> {
        self.writer.flush().await?;
        drop(self.writer);
        if let Some(temporary) = self.temporary {
            temporary
                .persist(&self.path)
                .map_err(|error| CliError::from(error.error))?;
        }
        Ok(json!({"file": self.path, "bytes": size}))
    }
}
