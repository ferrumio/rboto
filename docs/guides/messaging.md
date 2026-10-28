# SNS and SQS

Use `SNSClient()` to publish notifications and `SQSClient()` to send, receive, and delete queue
messages. Install their service packages from the checkout.

## Publish to an existing topic

```python
--8<-- "examples/docs/sns_publish.py"
```

Configure the client for the topic's region. A message ID confirms acceptance by
SNS; subscriber processing happens separately.

## Send to a queue

```python
--8<-- "examples/docs/typed_message.py"
```

The example uses a standard queue URL. FIFO queues have additional grouping and
deduplication requirements; supply their operation parameters for your queue.

## Receive and acknowledge

```python
--8<-- "examples/docs/sqs_receive.py"
```

Replace `print()` with your processing and delete each message after that work
succeeds. Receipt handles come from the receive response. Account for redelivery.
Long polling waits up to `wait_time_seconds`; an empty response is normal.
Batch methods report per-entry failures that need inspection before retrying.
