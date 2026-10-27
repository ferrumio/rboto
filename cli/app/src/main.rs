mod generated;

use rboto_cli_core::{CliError, Options, Output, Result, Value, json};
use serde::Deserialize;
use tokio::io::AsyncReadExt;

#[derive(Deserialize)]
struct Service {
    service: String,
    operations: Vec<Operation>,
}

#[derive(Deserialize)]
struct Operation {
    name: String,
    api_name: String,
    description: String,
    parameters: Vec<Parameter>,
    paginated: bool,
}

#[derive(Deserialize)]
struct Parameter {
    name: String,
    kind: String,
    required: bool,
    #[serde(rename = "type")]
    type_name: String,
}

#[derive(Default)]
struct Parsed {
    options: Options,
    input: Value,
    region: Option<String>,
    profile: Option<String>,
    attempts: Option<u32>,
    timeout: Option<u64>,
    validate_only: bool,
}

fn help() {
    println!(
        "rboto <service> <operation> [parameters]\n\
         rboto --services | --self-test | --version\n\
         rboto <service> --help\n\
         rboto <service> <operation> --help\n\n\
         --input FILE|JSON|-        Input JSON; member names use snake_case\n\
         --<parameter> VALUE        Set a parameter; objects/arrays use JSON\n\
         --param NAME=JSON          Set a parameter that conflicts with a global option\n\
         --bytes MEMBER=FILE        Binary input (also supports nested $file objects in JSON)\n\
         --events MEMBER=FILE|-     Input events as NDJSON, pulled on demand\n\
         --output-file FILE|-       Binary output; files are replaced only after success\n\
         --output-member MEMBER     Select a binary response member when needed\n\
         --profile NAME --region REGION --endpoint-url URL\n\
         --paginate [--max-pages N] Print each page as a separate JSON line\n\
         --pretty                   Indent regular JSON responses\n\
         --max-attempts N           SDK retry limit\n\
         --timeout SECONDS          Limit the entire operation\n\
         --validate                Validate names/required parameters without a request\n\n\
         Event responses use NDJSON: a response record followed by stream/event records.\n\
         Errors are JSON on stderr. Binary stdout uses stderr for response metadata."
    );
}

fn operation_help(service: &str, operation: &Operation) {
    println!(
        "rboto {service} {}\n\n{}",
        operation.name.replace('_', "-"),
        operation.description
    );
    println!("\nParameters (snake_case names also work in --input JSON):");
    for parameter in &operation.parameters {
        println!(
            "  --{:<38} {:<25} {}",
            parameter.name.replace('_', "-"),
            parameter.type_name,
            if parameter.required {
                "required"
            } else {
                "optional"
            },
        );
    }
    if operation.paginated {
        println!("\nSupports --paginate and --max-pages.");
    }
}

fn positive<T: std::str::FromStr + PartialEq + Default>(value: &str, option: &str) -> Result<T> {
    let parsed = value
        .parse::<T>()
        .map_err(|_| CliError::input(format!("{option} requires a positive integer")))?;
    if parsed == T::default() {
        return Err(CliError::input(format!(
            "{option} must be greater than zero"
        )));
    }
    Ok(parsed)
}

async fn parse(args: &[String], operation: &Operation) -> Result<Parsed> {
    let mut result = Parsed {
        input: json!({}),
        ..Parsed::default()
    };
    let mut input_source = None;
    let mut assignments: Vec<(String, Value)> = Vec::new();
    let mut args = args.iter();
    let mut events_stdin = false;
    while let Some(flag) = args.next() {
        match flag.as_str() {
            "--paginate" => {
                result.options.paginate = true;
                continue;
            }
            "--pretty" => {
                result.options.pretty = true;
                continue;
            }
            "--validate" => {
                result.validate_only = true;
                continue;
            }
            _ => {}
        }
        if !flag.starts_with("--") {
            return Err(CliError::input(format!("expected an option, found {flag}")));
        }
        let value = args
            .next()
            .ok_or_else(|| CliError::input(format!("{flag} requires a value")))?;
        match flag.as_str() {
            "--input" => {
                if input_source.replace(value.as_str()).is_some() {
                    return Err(CliError::input("--input may be specified only once"));
                }
            }
            "--region" => result.region = Some(value.clone()),
            "--profile" => result.profile = Some(value.clone()),
            "--endpoint-url" => result.options.endpoint_url = Some(value.clone()),
            "--output-file" => result.options.output_file = Some(value.clone()),
            "--output-member" => result.options.output_member = Some(value.replace('-', "_")),
            "--max-attempts" => result.attempts = Some(positive(value, flag)?),
            "--timeout" => result.timeout = Some(positive(value, flag)?),
            "--max-pages" => {
                result.options.max_pages = Some(positive(value, flag)?);
                result.options.paginate = true;
            }
            "--bytes" | "--events" | "--param" => {
                let (name, text) = value
                    .split_once('=')
                    .ok_or_else(|| CliError::input(format!("{flag} requires MEMBER=VALUE")))?;
                let name = name.replace('-', "_");
                let value = match flag.as_str() {
                    "--bytes" => json!({"$file": text}),
                    "--events" => {
                        if text == "-" {
                            events_stdin = true;
                        }
                        json!({"$events": text})
                    }
                    _ => serde_json::from_str(text)?,
                };
                assignments.push((name, value));
            }
            _ => {
                let name = flag.trim_start_matches("--").replace('-', "_");
                let parameter = operation
                    .parameters
                    .iter()
                    .find(|p| p.name == name)
                    .ok_or_else(|| CliError::input(format!("unknown option: {flag}")))?;
                let parsed = match parameter.kind.as_str() {
                    "string" | "enum" | "datetime" | "blob" | "byte_stream" => {
                        Value::String(value.clone())
                    }
                    _ => serde_json::from_str(value).map_err(|error| {
                        CliError::input(format!("{flag}: {error}; use JSON syntax"))
                    })?,
                };
                assignments.push((name, parsed));
            }
        }
    }
    if let Some(source) = input_source {
        result.input = if source.trim_start().starts_with('{') {
            serde_json::from_str(source)?
        } else if source == "-" {
            if events_stdin {
                return Err(CliError::input(
                    "stdin cannot supply both --input and --events",
                ));
            }
            let mut text = String::new();
            tokio::io::stdin().read_to_string(&mut text).await?;
            serde_json::from_str(&text)?
        } else {
            serde_json::from_slice(&tokio::fs::read(source.trim_start_matches('@')).await?)?
        };
    }
    let values = result
        .input
        .as_object_mut()
        .ok_or_else(|| CliError::input("--input must contain an object"))?;
    let mut assigned = std::collections::HashSet::new();
    for (name, value) in assignments {
        if !assigned.insert(name.clone()) {
            return Err(CliError::input(format!(
                "parameter specified more than once: {name}"
            )));
        }
        values.insert(name, value);
    }
    let allowed: Vec<&str> = operation
        .parameters
        .iter()
        .map(|p| p.name.as_str())
        .collect();
    let required: Vec<&str> = operation
        .parameters
        .iter()
        .filter(|p| p.required)
        .map(|p| p.name.as_str())
        .collect();
    let stdin_sources = values
        .values()
        .filter(|value| value.get("$events").and_then(Value::as_str) == Some("-"))
        .count();
    if stdin_sources > 1 || (stdin_sources != 0 && input_source == Some("-")) {
        return Err(CliError::input("stdin may supply only one input source"));
    }
    rboto_cli_core::check_fields(&result.input, &allowed, &required)?;
    if result.options.paginate && !operation.paginated {
        return Err(CliError::input("this operation has no paginator"));
    }
    Ok(result)
}

async fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() || args[0] == "--help" || args[0] == "-h" {
        help();
        return Ok(());
    }
    if args[0] == "--version" {
        println!("rboto {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    let catalog: Vec<Service> = serde_json::from_str(generated::CATALOG)?;
    if args[0] == "--services" {
        println!(
            "{}",
            json!(catalog.iter().map(|s| &s.service).collect::<Vec<_>>())
        );
        return Ok(());
    }
    if args[0] == "--self-test" {
        let mut count = 0;
        let compiled = generated::compiled_operations();
        if compiled.len() != catalog.len() {
            return Err(CliError::new(
                "CatalogError",
                "compiled service count differs from catalog",
            ));
        }
        for (service, operations) in compiled {
            let expected = catalog
                .iter()
                .find(|s| s.service == service)
                .ok_or_else(|| CliError::new("CatalogError", service))?;
            let names: Vec<&str> = expected
                .operations
                .iter()
                .map(|o| o.name.as_str())
                .collect();
            if names != operations {
                return Err(CliError::new(
                    "CatalogError",
                    format!("{service}: operation mismatch"),
                ));
            }
            count += operations.len();
        }
        println!(
            "{}",
            json!({"services": catalog.len(), "operations": count})
        );
        return Ok(());
    }
    let service = catalog
        .iter()
        .find(|service| service.service == args[0].replace('_', "-"))
        .ok_or_else(|| CliError::input(format!("unknown service: {}", args[0])))?;
    if args.len() == 1 || args[1] == "--help" || args[1] == "-h" {
        println!("{}\n", service.service);
        for operation in &service.operations {
            println!("  {}", operation.name.replace('_', "-"));
        }
        return Ok(());
    }
    let operation = service
        .operations
        .iter()
        .find(|op| op.name == args[1].replace('-', "_") || op.api_name == args[1])
        .ok_or_else(|| CliError::input(format!("unknown operation: {}", args[1])))?;
    if args[2..].iter().any(|arg| arg == "--help" || arg == "-h") {
        operation_help(&service.service, operation);
        return Ok(());
    }
    let parsed = parse(&args[2..], operation).await?;
    if parsed.validate_only {
        println!(
            "{}",
            json!({"service": service.service, "operation": operation.name, "parameters": parsed.input})
        );
        return Ok(());
    }
    let mut loader = aws_config::defaults(aws_config::BehaviorVersion::latest());
    if let Some(region) = parsed.region {
        loader = loader.region(aws_config::Region::new(region));
    }
    if let Some(profile) = parsed.profile {
        loader = loader.profile_name(profile);
    }
    if let Some(attempts) = parsed.attempts {
        loader = loader
            .retry_config(aws_config::retry::RetryConfig::standard().with_max_attempts(attempts));
    }
    let request = async {
        let config = loader.load().await;
        generated::dispatch(
            &service.service,
            &operation.name,
            &config,
            &parsed.input,
            &parsed.options,
        )
        .await
    };
    let request = async {
        match parsed.timeout {
            Some(seconds) => tokio::time::timeout(std::time::Duration::from_secs(seconds), request)
                .await
                .map_err(|_| CliError::new("TimeoutError", "operation exceeded --timeout"))?,
            None => request.await,
        }
    };
    tokio::select! {
        result = request => result,
        result = tokio::signal::ctrl_c() => {
            result?;
            Err(CliError::new("Interrupted", "operation interrupted"))
        }
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    if let Err(error) = run().await {
        if error.error == "BrokenPipe" {
            std::process::exit(0);
        }
        let status = if error.error == "Interrupted" {
            130
        } else if error.error == "InputError" {
            2
        } else {
            1
        };
        // Keep stdout suitable for piping JSON or binary data.
        let _ = Output::new(Options {
            output_file: Some("-".into()),
            ..Options::default()
        })
        .emit(&serde_json::to_value(&error).unwrap_or_else(|_| json!({"error": "InternalError"})))
        .await;
        std::process::exit(status);
    }
    // A server may finish while Tokio's blocking stdin reader still awaits input.
    // Outputs are flushed and operation-owned temporary files dropped before exit.
    std::process::exit(0);
}
