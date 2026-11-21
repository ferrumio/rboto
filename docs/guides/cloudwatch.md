# CloudWatch

Install `rboto[cloudwatch]` and import `CloudWatchClient` from `rboto`.
This client manages metrics, alarms, dashboards, and metric streams. Log groups
and log events use the separate `CloudWatchLogsClient`.

## Publish a metric and list alarms

Configure credentials with permission to publish metrics and describe alarms:

```python
--8<-- "examples/clients/cloudwatch.py"
```

The example publishes a custom metric in `eu-west-2`. Custom metrics can incur
charges. Change the namespace, dimensions, and region to match your application.

## Query metrics and wait for alarms

Use `client.get_metric_data(...)` for one response or
`client.paginators.get_metric_data(...)` to follow continuation tokens.
`metric_data_queries` accepts typed mappings; results expose native attributes
such as `metric_data_results`, `timestamps`, and `values`.

`client.waiters.alarm_exists(...)`, `composite_alarm_exists(...)`,
`log_alarm_exists(...)`, and `alarm_mute_rule_exists(...)` wait for resource
existence. They do not wait for an alarm to enter `ALARM` or `OK`.
Set a bounded `WaiterConfig` as described in [Waiters](waiters.md).

Import input and output types from `rboto_cloudwatch.types`, and catch modeled
exceptions from `rboto_cloudwatch.exceptions`. For example,
`InvalidParameterValueError` identifies a rejected metric value or parameter.
`ResourceNotFoundError` and `ResourceNotFoundExceptionError` are separate modeled
errors; each operation's reference lists which one it can raise.

See the [CloudWatch operation reference](../reference/cloudwatch/index.md) for
all methods, paginator signatures, waiters, and output fields.
