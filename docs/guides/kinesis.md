# Kinesis Data Streams

Install `rboto[kinesis]` and import `KinesisClient` from `rboto`.
The client exposes stream and consumer management, record writes and reads,
and enhanced fan-out subscriptions.

## Write a record

Create a stream first, then set `KINESIS_STREAM_NAME` to its name. Configure
credentials with permission to write records in the stream's region:

```python
--8<-- "examples/clients/kinesis.py"
```

Pass raw `bytes`; the SDK performs the wire encoding. A successful write returns
a typed output with `shard_id` and `sequence_number`.

For `put_records(...)`, inspect `failed_record_count` and each entry in `records`.
A successful HTTP request can contain individual write failures. Retrying the
whole batch can duplicate records that already succeeded.

## Read records

Call `get_shard_iterator(...)`, then pass its `shard_iterator` to
`get_records(...)`. Records expose `data` as `bytes`, along with partition keys
and sequence numbers. Continue with `next_shard_iterator`, using a bounded
polling loop and an appropriate delay; an empty response does not mean the
stream has ended.

`get_records` is not a finite paginator. For enhanced fan-out, use
`subscribe_to_shard(...)` with a registered consumer. Its event stream is an
async iterator; close it when finished. See [Streaming](streaming.md).

## Pagination and waiters

Use `client.paginators.list_streams(...)`, `list_shards(...)`,
`list_stream_consumers(...)`, `describe_stream(...)`, `list_tags_for_stream(...)`,
and `list_channels(...)` for finite listings.

Shard pagination drops the initial stream name, creation timestamp, and starting
shard ID when following a continuation token. Consumer pagination drops the
initial creation timestamp. Other parameters remain unchanged.

The modeled waiters are `stream_exists`, `stream_not_exists`, and `channel_active`.
Use a bounded `WaiterConfig`; see [Waiters](waiters.md).

Import types from `rboto_kinesis.types` and exceptions from
`rboto_kinesis.exceptions`. See the
[Kinesis operation reference](../reference/kinesis/index.md) for the complete API.
