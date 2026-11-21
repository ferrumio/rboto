# Data Firehose

Install `rboto[firehose]` and import `FirehoseClient` from `rboto`.
Use it to write records and manage delivery streams.

## Write a record

Create a delivery stream that accepts direct writes, then set
`FIREHOSE_DELIVERY_STREAM_NAME` to its name. Configure credentials with permission
to write records in its region:

```python
--8<-- "examples/clients/firehose.py"
```

Pass raw `bytes`; do not base64-encode them yourself. This example appends a
newline so concatenated JSON records can be read as newline-delimited JSON.
A successful `put_record` acknowledges ingestion into Firehose; it does not
confirm that the destination has received the record.

## Batch writes

`put_record_batch(delivery_stream_name=..., records=[{"data": ...}])` returns
`failed_put_count` and `request_responses` in input order. Check each entry's
`error_code` and `error_message`, and retry only failed records. Retrying the
whole batch can duplicate successful writes.

## List delivery streams and tags

Use `client.paginators.list_delivery_streams(...)`,
`list_tags_for_delivery_stream(...)`, or `describe_delivery_stream(...)`.
These use the service's last-item cursors and completion flags.

The pinned model defines no waiters. When creating or updating a delivery stream,
poll `describe_delivery_stream(...)` with a bounded timeout and inspect its
status. Closing a client releases local resources; it does not stop delivery.

Import types from `rboto_firehose.types` and exceptions from
`rboto_firehose.exceptions`. See the
[Firehose operation reference](../reference/firehose/index.md) for all methods.
