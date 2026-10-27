use std::collections::{HashMap, VecDeque};
use std::pin::Pin;

use aws_smithy_types::{Blob, DateTime, Document, Number, byte_stream::ByteStream};
use base64::Engine;
use futures_util::{Stream, StreamExt, stream};
use serde::de::DeserializeOwned;
use serde_json::{Map, Value};
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, BufReader};

use crate::{CliError, Result};

pub fn object(value: &Value) -> Result<&Map<String, Value>> {
    value
        .as_object()
        .ok_or_else(|| CliError::input("expected a JSON object"))
}

pub fn array(value: &Value) -> Result<&Vec<Value>> {
    value
        .as_array()
        .ok_or_else(|| CliError::input("expected a JSON array"))
}

pub fn from_json<T: DeserializeOwned>(value: &Value) -> Result<T> {
    Ok(serde_json::from_value(value.clone())?)
}

pub fn optional<'a>(values: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    values.get(key).filter(|value| !value.is_null())
}

pub fn check_fields(value: &Value, allowed: &[&str], required: &[&str]) -> Result<()> {
    let values = object(value)?;
    for key in values.keys() {
        if !allowed.contains(&key.as_str()) {
            return Err(CliError::input(format!("unknown parameter: {key}")));
        }
    }
    for key in required {
        if optional(values, key).is_none() {
            return Err(CliError::input(format!(
                "missing required parameter: {key}"
            )));
        }
    }
    Ok(())
}

pub fn parse_timestamp(value: &Value) -> Result<DateTime> {
    let text: String = from_json(value)?;
    DateTime::from_str(&text, aws_smithy_types::date_time::Format::DateTime)
        .map_err(|error| CliError::input(error.to_string()))
}

fn file_reference<'a>(value: &'a Value, key: &str) -> Result<Option<&'a str>> {
    if let Some(values) = value.as_object() {
        if values.len() != 1 || !values.contains_key(key) {
            return Err(CliError::input(format!(
                "expected an object containing only {key}"
            )));
        }
        return values[key]
            .as_str()
            .map(Some)
            .ok_or_else(|| CliError::input(format!("{key} must be a file path")));
    }
    Ok(None)
}

pub fn blob(value: &Value) -> Result<Blob> {
    if let Some(path) = file_reference(value, "$file")? {
        if path == "-" {
            return Err(CliError::input(
                "binary inputs require a file path; stdin is supported for JSON and events",
            ));
        }
        return Ok(Blob::new(std::fs::read(path)?));
    }
    let text: String = from_json(value)?;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(text)
        .map_err(|error| CliError::input(format!("invalid base64: {error}")))?;
    Ok(Blob::new(bytes))
}

pub async fn byte_stream(value: &Value) -> Result<ByteStream> {
    if let Some(path) = file_reference(value, "$file")? {
        return ByteStream::from_path(path)
            .await
            .map_err(|error| CliError::input(error.to_string()));
    }
    Ok(ByteStream::from(blob(value)?.into_inner()))
}

pub fn document(value: &Value) -> Result<Document> {
    Ok(match value {
        Value::Null => Document::Null,
        Value::Bool(value) => Document::Bool(*value),
        Value::String(value) => Document::String(value.clone()),
        Value::Array(values) => {
            Document::Array(values.iter().map(document).collect::<Result<_>>()?)
        }
        Value::Object(values) => Document::Object(
            values
                .iter()
                .map(|(key, value)| Ok((key.clone(), document(value)?)))
                .collect::<Result<HashMap<_, _>>>()?,
        ),
        Value::Number(value) => Document::Number(if let Some(value) = value.as_u64() {
            Number::PosInt(value)
        } else if let Some(value) = value.as_i64() {
            Number::NegInt(value)
        } else {
            Number::Float(
                value
                    .as_f64()
                    .ok_or_else(|| CliError::input("invalid number"))?,
            )
        }),
    })
}

pub fn document_json(value: &Document) -> Result<Value> {
    Ok(match value {
        Document::Null => Value::Null,
        Document::Bool(value) => Value::Bool(*value),
        Document::String(value) => Value::String(value.clone()),
        Document::Array(values) => {
            Value::Array(values.iter().map(document_json).collect::<Result<_>>()?)
        }
        Document::Object(values) => Value::Object(
            values
                .iter()
                .map(|(key, value)| Ok((key.clone(), document_json(value)?)))
                .collect::<Result<_>>()?,
        ),
        Document::Number(Number::PosInt(value)) => Value::from(*value),
        Document::Number(Number::NegInt(value)) => Value::from(*value),
        Document::Number(Number::Float(value)) => Value::Number(
            serde_json::Number::from_f64(*value)
                .ok_or_else(|| CliError::input("non-finite document number"))?,
        ),
    })
}

enum EventSource {
    Array(VecDeque<Value>),
    Reader(Pin<Box<dyn AsyncBufRead + Send + Sync>>),
}

impl EventSource {
    async fn next(&mut self) -> Result<Option<Value>> {
        match self {
            Self::Array(values) => Ok(values.pop_front()),
            Self::Reader(reader) => loop {
                let mut line = String::new();
                // Bound a single NDJSON event, without an unbounded producer queue.
                let read = reader
                    .as_mut()
                    .take(16 * 1024 * 1024 + 1)
                    .read_line(&mut line)
                    .await?;
                if read == 0 {
                    return Ok(None);
                }
                if read > 16 * 1024 * 1024 {
                    return Err(CliError::input("an event line exceeds 16 MiB"));
                }
                if !line.trim().is_empty() {
                    return Ok(Some(serde_json::from_str(&line)?));
                }
            },
        }
    }
}

pub async fn event_stream<T, E>(
    value: &Value,
    convert: fn(&Value) -> Result<T>,
    map_error: fn(CliError) -> E,
) -> Result<impl Stream<Item = std::result::Result<T, E>> + Send + Sync + 'static>
where
    T: Send + Sync + 'static,
    E: Send + Sync + 'static,
{
    let source = if let Some(path) = file_reference(value, "$events")? {
        let reader: Pin<Box<dyn AsyncBufRead + Send + Sync>> = if path == "-" {
            Box::pin(BufReader::new(tokio::io::stdin()))
        } else {
            Box::pin(BufReader::new(tokio::fs::File::open(path).await?))
        };
        EventSource::Reader(reader)
    } else {
        EventSource::Array(array(value)?.clone().into())
    };
    Ok(
        stream::unfold((source, false), move |(mut source, done)| async move {
            if done {
                return None;
            }
            let item = match source.next().await {
                Ok(None) => return None,
                Ok(Some(value)) => convert(&value),
                Err(error) => Err(error),
            };
            let done = item.is_err();
            Some((item.map_err(map_error), (source, done)))
        })
        .fuse(),
    )
}
