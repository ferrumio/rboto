import json

from rboto import eventbridge, pipes, scheduler
from rboto_eventbridge import PutEventsResponse
from rboto_eventbridge.types import PutEventsRequestEntry
from rboto_pipes import CreatePipeResponse
from rboto_pipes.types import PipeSourceParameters
from rboto_scheduler import CreateScheduleOutput
from rboto_scheduler.types import FlexibleTimeWindow, Target


async def publish(bus_name: str) -> PutEventsResponse:
    entry: PutEventsRequestEntry = {
        "event_bus_name": bus_name,
        "source": "rboto.example",
        "detail_type": "Example",
        "detail": json.dumps({"value": 42}),
        "time": "2026-10-26T00:00:00Z",
    }
    response = await eventbridge().put_events(entries=[entry])
    failures: int = response.failed_entry_count
    assert failures >= 0
    for result in response.entries or []:
        event_id: str | None = result.event_id
        assert event_id is not None or result.error_code is not None
    return response


async def schedule(queue_arn: str, role_arn: str) -> CreateScheduleOutput:
    target: Target = {
        "arn": queue_arn,
        "role_arn": role_arn,
        "input": json.dumps({"value": "scheduled"}),
        "retry_policy": {"maximum_retry_attempts": 0},
    }
    window: FlexibleTimeWindow = {"mode": "OFF"}
    return await scheduler().create_schedule(
        name="example",
        schedule_expression="rate(1 day)",
        schedule_expression_timezone="America/Sao_Paulo",
        flexible_time_window=window,
        target=target,
    )


async def connect(source_arn: str, target_arn: str, role_arn: str) -> CreatePipeResponse:
    source: PipeSourceParameters = {
        "sqs_queue_parameters": {"batch_size": 1, "maximum_batching_window_in_seconds": 0},
        "filter_criteria": {"filters": [{"pattern": '{"body":{"kind":["example"]}}'}]},
    }
    return await pipes().create_pipe(
        name="example",
        source=source_arn,
        target=target_arn,
        role_arn=role_arn,
        source_parameters=source,
        target_parameters={"input_template": '{"kind":"<$.body.kind>"}'},
    )
