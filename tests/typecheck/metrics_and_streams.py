"""Application contracts for metrics, record batches, and event subscriptions."""

from typing import assert_type

from rboto import CloudWatchClient, FirehoseClient, KinesisClient
from rboto.waiters import WaiterConfig
from rboto_cloudwatch.types import GetMetricDataOutput, MetricDatum
from rboto_firehose.types import PutRecordBatchOutput
from rboto_firehose.types import Record as FirehoseRecord
from rboto_kinesis.types import GetRecordsOutput, PutRecordsRequestEntry


async def metrics(client: CloudWatchClient) -> None:
    metric: MetricDatum = {"metric_name": "Processed", "value": 1.0, "unit": "Count"}
    await client.put_metric_data(namespace="Example", metric_data=[metric])
    async for page in client.paginators.get_metric_data(
        start_time="2026-11-01T00:00:00Z",
        end_time="2026-11-01T01:00:00Z",
        metric_data_queries=[{"id": "m1", "expression": "1+1"}],
    ):
        assert_type(page, GetMetricDataOutput)
        for result in page.metric_data_results or []:
            assert_type(result.values, list[float] | None)
    await client.waiters.alarm_exists(
        alarm_names=["example"], waiter_config=WaiterConfig(max_attempts=3),
    )


async def records(client: KinesisClient, stream_arn: str, consumer_arn: str) -> None:
    entry: PutRecordsRequestEntry = {"partition_key": "key", "data": b"payload"}
    batch = await client.put_records(stream_arn=stream_arn, records=[entry])
    assert_type(batch.failed_record_count, int | None)
    result = await client.get_records(shard_iterator="iterator")
    assert_type(result, GetRecordsOutput)
    for record in result.records:
        assert_type(record.data, bytes)
    async for page in client.paginators.list_shards(stream_arn=stream_arn):
        for shard in page.shards or []:
            assert_type(shard.shard_id, str)
    subscription = await client.subscribe_to_shard(
        consumer_arn=consumer_arn, shard_id="shard-0", starting_position={"type": "LATEST"},
    )
    async for event in subscription["event_stream"]:
        if "subscribe_to_shard_event" in event:
            for record_event in event["subscribe_to_shard_event"]["records"]:
                assert_type(record_event["data"], bytes)


async def delivery(client: FirehoseClient) -> None:
    record: FirehoseRecord = {"data": b"payload"}
    batch = await client.put_record_batch(delivery_stream_name="example", records=[record])
    assert_type(batch, PutRecordBatchOutput)
    assert_type(batch.failed_put_count, int)
    for entry in batch.request_responses:
        assert_type(entry.record_id, str | None)
        assert_type(entry.error_code, str | None)
    async for page in client.paginators.list_delivery_streams():
        assert_type(page.delivery_stream_names, list[str])
