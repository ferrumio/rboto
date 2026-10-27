use aws_smithy_types::byte_stream::ByteStream;
use futures_util::{StreamExt, pin_mut};
use rboto_cli_core::{
    CliError, Options, Output, Value, blob, check_fields, document, document_json, event_stream,
    from_json, json,
};

#[test]
fn documents_preserve_nested_values_and_integer_limits() {
    let input = json!({
        "unsigned": u64::MAX, "signed": i64::MIN,
        "nested": [null, true, false, 1.25, {"value": "text"}],
    });
    assert_eq!(document_json(&document(&input).unwrap()).unwrap(), input);
}

#[test]
fn parameters_reject_unknown_names_missing_members_and_invalid_scalars() {
    assert!(check_fields(&json!({"typo": 1}), &["value"], &[]).is_err());
    assert!(check_fields(&json!({"value": null}), &["value"], &["value"]).is_err());
    assert!(from_json::<i32>(&json!(i64::MAX)).is_err());
    assert!(from_json::<bool>(&json!("false")).is_err());
    check_fields(&json!({"value": false}), &["value"], &["value"]).unwrap();
}

#[test]
fn blobs_decode_base64_and_explicit_file_references() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("payload");
    std::fs::write(&path, [0_u8, 255, 42]).unwrap();
    assert_eq!(blob(&json!("AP8q")).unwrap().as_ref(), &[0, 255, 42]);
    assert_eq!(
        blob(&json!({"$file": path})).unwrap().as_ref(),
        &[0, 255, 42]
    );
    assert!(blob(&json!("not base64!")).is_err());
    assert!(blob(&json!({"$file": path, "ignored": true})).is_err());
}

#[tokio::test]
async fn event_files_are_parsed_on_demand_and_stop_after_an_error() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.ndjson");
    std::fs::write(&path, "{\"value\":1}\ninvalid json\n{\"value\":3}\n").unwrap();
    fn convert(value: &Value) -> rboto_cli_core::Result<Value> {
        Ok(value.clone())
    }
    fn error(error: CliError) -> CliError {
        error
    }
    let source = event_stream(&json!({"$events": path}), convert, error)
        .await
        .unwrap();
    pin_mut!(source);
    assert_eq!(source.next().await.unwrap().unwrap(), json!({"value": 1}));
    assert!(source.next().await.unwrap().is_err());
    assert!(source.next().await.is_none());
}

#[tokio::test]
async fn file_output_commits_bytes_and_leaves_no_temporary_file() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("download");
    std::fs::write(&path, b"original").unwrap();
    let output = Output::new(Options {
        output_file: Some(path.to_str().unwrap().into()),
        output_member: Some("body".into()),
        ..Options::default()
    });
    let result = output
        .stream("body", ByteStream::from_static(b"replacement"))
        .await
        .unwrap();
    assert_eq!(result["bytes"], 11);
    assert_eq!(std::fs::read(&path).unwrap(), b"replacement");
    assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
}

#[test]
fn file_output_requires_an_unambiguous_binary_member() {
    let mut options = Options {
        output_file: Some("output".into()),
        ..Options::default()
    };
    assert!(options.validate_output(&[], false).is_err());
    assert!(
        options
            .validate_output(&["first", "second"], false)
            .is_err()
    );
    options.output_member = Some("first".into());
    options
        .validate_output(&["first", "second"], false)
        .unwrap();
    options.paginate = true;
    assert!(options.validate_output(&["first"], false).is_err());
}
