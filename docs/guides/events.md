# Events and schedules

Install the relevant client packages from the checkout; see [Getting started](../getting-started.md).
Examples use existing resources and require permission for the requested operation.

These are three independent clients:

| Client | Purpose |
|---|---|
| `eventbridge()` | Event buses, events, rules, targets, archives, and replays |
| `scheduler()` | One-time and recurring schedules, time zones, and flexible time windows |
| `pipes()` | Connect event sources to targets with filtering and optional enrichment |

Traditional scheduled rules belong to `eventbridge()`. EventBridge Scheduler has its own
`scheduler()` client.

Publish a typed event to an existing bus:

```python
--8<-- "examples/docs/events_1.py"
```

`PutEvents` reports per-entry failures in a successful HTTP response. Check
`failed_entry_count` and each entry's `error_code`.

Create a schedule using an existing target and execution role:

```python
--8<-- "examples/docs/events_2.py"
```

Create a pipe between existing SQS queues:

```python
--8<-- "examples/docs/events_3.py"
```

The execution roles must trust their respective services and have access to their
sources and targets. Pipe creation is asynchronous; use `describe_pipe()` to check
`current_state`. Remove resources with `delete_schedule()` and `delete_pipe()` when done.
