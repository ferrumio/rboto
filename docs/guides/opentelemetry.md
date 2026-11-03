# OpenTelemetry

Install the integration alongside the services you use:

```bash
python -m pip install "rboto[s3,dynamodb,opentelemetry]"
```

rboto adds one client span per operation and optional operation metrics. It uses
your application's OpenTelemetry providers. It does not install an SDK, exporter,
collector, background worker, or global provider.

## Use your application's providers

If your application already configures OpenTelemetry, enable rboto once:

```python
from rboto.integrations.opentelemetry import configure_opentelemetry

configure_opentelemetry()
```

This applies to all services, including clients created before configuration.
With no application providers configured, the OpenTelemetry API does not export
anything.

You can supply a tracer and meter directly, or their providers:

```python
from opentelemetry import metrics, trace
from rboto.integrations.opentelemetry import configure_opentelemetry

configure_opentelemetry(
    tracer=trace.get_tracer("my-application"),
    meter=metrics.get_meter("my-application"),
    record_attempts=True,
)
```

Use `tracer_provider=` and `meter_provider=` instead when rboto should obtain
instruments from those providers. Do not supply both a tracer and its provider,
or both a meter and its provider. rboto never shuts down supplied providers.

Calls made inside an active application span become its children:

```python
import asyncio

from opentelemetry import trace
from rboto import DynamoDBClient, S3Client

tracer = trace.get_tracer("my-application")
s3 = S3Client(region="us-east-1")
dynamodb = DynamoDBClient(region="us-east-1")

async def inspect_resources() -> None:
    with tracer.start_as_current_span("inspect-resources"):
        buckets, tables = await asyncio.gather(
            s3.list_buckets(),
            dynamodb.list_tables(),
        )
        print(buckets.buckets)
        print(tables.table_names)
```

Each concurrent operation keeps its own span context. Instrumentation does not
change response types, modeled exceptions, native retries, or stream ownership.
Paginator pages and waiter polls each call an operation and therefore get their
own operation span.

## Scope and disabling

Tracing and metrics are independent:

```python
configure_opentelemetry(tracing=True, metrics=False)
configure_opentelemetry(tracing=False, metrics=True)
configure_opentelemetry(enabled=False)
```

Each call replaces the global configuration; repeated initialization does not
stack observers. An operation already running finishes with its original
configuration.

For one client or `Session`, supply `OpenTelemetry` through `interceptors`:

```python
from rboto import S3Client, Session
from rboto.integrations.opentelemetry import OpenTelemetry

session = Session(
    region="us-east-1",
    interceptors=(OpenTelemetry(metrics=False),),
)
s3 = S3Client(session=session)

quiet_s3 = S3Client(interceptors=(OpenTelemetry(enabled=False),))
```

An explicit instance overrides global instrumentation. A client can replace its
Session's interceptor sequence as described in [Interceptors](interceptors.md).
An empty sequence removes inherited interceptors, but global instrumentation
still applies; use `OpenTelemetry(enabled=False)` to disable it for that client.
Disabling the global configuration does not disable explicit instances.

rboto automatically places the single telemetry observer outside logging and
application interceptors, regardless of its position in the supplied sequence.
Two explicit telemetry observers in one sequence are rejected.

## What a span measures

A span covers the logical operation: application interceptors, native dispatch,
native retries, and response postprocessing. A cached response counts as a
successful operation with zero native attempts. A policy rejection counts as
an error even when no request was sent. If an interceptor recovers a native
failure, the span reports success and retains separate native failure metadata.

The span ends when the operation returns or raises. Reading an S3 response body
or consuming an event stream afterward is outside its duration. Instrumentation
does not read streams to collect telemetry.

Uncaught failures set error status and record the exception class. Exception
messages, tracebacks, inputs, outputs, credentials, object keys, resource names,
and arbitrary headers are not recorded. Cancellation retains unset span status
and records `rboto.operation.outcome = "cancelled"`. It is not counted as an error.
Failures in telemetry delivery do not replace the operation's result or exception.

With `record_attempts=True`, spans include up to 32 `rboto.attempt` events from
the native collector, with attempt number, duration in seconds, status, and
request ID when available. Total attempt/retry counts can exceed that event
limit. Events are attached at operation completion; they are not independent
spans or timestamps of attempt starts. Cancellation can leave native attempt
counts unknown.

## Attributes and conventions

rboto follows the **Development AWS SDK span conventions from semantic
conventions 1.39.0**: `Service.Operation` names, client span kind,
`rpc.system = "aws-api"`, `rpc.service`, `rpc.method`, `cloud.region`, and
`aws.request_id`. Service names come from the pinned service model's `sdkId`.
The scoped schema URL is `https://opentelemetry.io/schemas/1.39.0` when rboto
obtains the tracer or meter; explicitly supplied instruments retain their own
scope.

Those SDK conventions still use `rpc.system` and `rpc.service`, which the newer
general RPC conventions deprecate. rboto pins this vocabulary instead of changing
exported attributes when an application's dependencies update.
See the [versioned SDK conventions](https://github.com/open-telemetry/semantic-conventions/blob/v1.39.0/docs/cloud-providers/aws-sdk.md).

Additional span attributes include `http.response.status_code`,
`rboto.operation.outcome`, `rboto.native.dispatched`, native attempt/retry counts,
and native error class when available. Service-specific payload attributes are
not collected. For example, an S3 call does not receive DynamoDB database
attributes. No trace headers are injected into signed requests or messages.

## Metrics

These are rboto instruments, not a claim of stable OpenTelemetry SDK metric
conventions:

| Instrument | Unit | Meaning |
| --- | --- | --- |
| `rboto.client.operation.duration` | `s` | Histogram of completed logical operation duration |
| `rboto.client.operation.count` | `{operation}` | Counter including success, failure, cancellation, and short circuits |
| `rboto.client.operation.errors` | `{error}` | Counter of uncaught failures, excluding cancellation |
| `rboto.client.operation.retries` | `{retry}` | Counter of known native retries, not logical operations |

All instruments use only `rboto.service`, `rboto.operation`, and `rboto.outcome`
as dimensions. Outcomes are `success`, `error`, and `cancelled`. Request IDs,
trace IDs, regions, resource names, and exception messages are not metric labels.
Unknown retry counts contribute no retry measurement. The application's metric
reader controls aggregation and export.

## Logs and local inspection

Enable [structured logging](logging.md) separately:

```python
from rboto import configure_logging

configure_logging()
configure_opentelemetry()
```

Operation records automatically receive the current rboto span's trace and span
IDs, including valid unsampled contexts. An explicit logging `trace_context`
callback takes precedence. Metrics and tracing also work when logging is disabled.

For local inspection, install `opentelemetry-sdk` and attach a console exporter:

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
configure_opentelemetry(tracer_provider=provider, metrics=False)
```

Console output and synchronous processors run on the calling thread. Choose
processors and exporters for your application's latency requirements.
See [instrumentation measurements](https://github.com/ferrumio/rboto/blob/main/benchmarks/opentelemetry.md)
for the local benchmark methodology and costs.

## Lambda

Configure providers and rboto instrumentation once, during module initialization,
after your Lambda telemetry setup. Keep clients outside the handler and use the
invocation span supplied by your application or Lambda instrumentation as the
parent. rboto does not extract an invocation context from the event.

```python
import asyncio

from rboto import S3Client
from rboto.integrations.opentelemetry import configure_opentelemetry

configure_opentelemetry()
s3 = S3Client()

async def list_buckets() -> dict[str, int]:
    response = await s3.list_buckets()
    return {"bucket_count": len(response.buckets or [])}

def handler(event: dict[str, object], context: object) -> dict[str, int]:
    return asyncio.run(list_buckets())
```

Completed operations restore the caller's context across repeated invocations
and event loops. Exporter flushing and shutdown remain the responsibility of
your application's telemetry setup. See [Lambda usage](aws-lambda.md).
