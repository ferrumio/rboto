mod error;
mod input;
mod output;

pub use error::{CliError, Result, api_error, stream_error};
pub use input::{
    array, blob, byte_stream, check_fields, document, document_json, event_stream, from_json,
    object, optional, parse_timestamp,
};
pub use output::{Output, bytes_json};
pub use serde_json::{Map, Value, json};

pub type CommandFuture<'a> =
    std::pin::Pin<Box<dyn std::future::Future<Output = Result<()>> + Send + 'a>>;

#[derive(Clone, Debug, Default)]
pub struct Options {
    pub paginate: bool,
    pub max_pages: Option<usize>,
    pub endpoint_url: Option<String>,
    pub output_member: Option<String>,
    pub output_file: Option<String>,
    pub pretty: bool,
}

impl Options {
    pub fn validate_output(&self, members: &[&str], requires_file: bool) -> Result<()> {
        if self.output_member.is_some() && self.output_file.is_none() {
            return Err(CliError::input("--output-member requires --output-file"));
        }
        if self.output_file.is_some() {
            if members.is_empty() {
                return Err(CliError::input(
                    "this operation has no binary output member",
                ));
            }
            if let Some(member) = &self.output_member {
                if !members.contains(&member.as_str()) {
                    return Err(CliError::input(format!(
                        "binary output members: {}",
                        members.join(", ")
                    )));
                }
            } else if members.len() != 1 {
                return Err(CliError::input(
                    "select a binary field with --output-member",
                ));
            }
            if self.paginate {
                return Err(CliError::input(
                    "binary file output cannot be combined with --paginate",
                ));
            }
        } else if requires_file {
            return Err(CliError::input(
                "this operation returns a byte stream; provide --output-file PATH (or - for stdout)",
            ));
        }
        Ok(())
    }

    pub fn selects(&self, member: &str) -> bool {
        self.output_file.is_some()
            && self
                .output_member
                .as_deref()
                .is_none_or(|selected| selected == member)
    }
}
