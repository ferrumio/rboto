# rboto

rboto provides typed, async-only Python clients for AWS services. Create a client,
await an operation, and read the response through typed attributes.

```python
--8<-- "examples/docs/first_request.py"
```

This request prints the account and principal used by your application. It needs
configured credentials and does not create resources.

rboto is under development. Packages have not yet been published to PyPI.
Follow [Getting started](getting-started.md) to install a client from source.

## Choose your clients

Each service has its own package. An application using S3 and DynamoDB needs those
two clients and the small `rboto` facade.

| Task | Clients | Guide |
|---|---|---|
| Upload or download objects | `S3Client()` | [S3](guides/s3.md) |
| Read and write items | `DynamoDBClient()` | [DynamoDB](guides/dynamodb.md) |
| Publish notifications or process queue messages | `SNSClient()`, `SQSClient()` | [Messaging](guides/messaging.md) |
| Invoke functions or models | `LambdaClient()`, `BedrockRuntimeClient()` | [Lambda and Bedrock](guides/inference.md) |
| Route events or schedule work | `EventBridgeClient()`, `EventBridgeSchedulerClient()`, `EventBridgePipesClient()` | [Events and schedules](guides/events.md) |
| Read configuration, encrypt values, start workflows, send email | `SSMClient()`, `KMSClient()`, `StepFunctionsClient()`, `SESV2Client()` | [Application services](guides/application-services.md) |

The [client catalog](clients.md) lists every service, its package, and its factory.

## Using the API

**Every service operation requires `await`.** In a script, start the event loop
once with `asyncio.run()`. In an async application, use the existing loop.
See [Async-only](guides/async-only.md).

**Inputs use keyword arguments and typed dictionaries.** Regular responses expose
read-only attributes such as `response.account`. See [Types and responses](guides/types.md).

**Clients are reusable.** Configuration loads on first use. Keep clients around
for later requests. See [Configuration and credentials](guides/configuration.md).

**Streams are consumed as you read them.** Use `async for` for chunks or events,
and `.read()` for byte streams that fit in memory. See [Streaming](guides/streaming.md).

Signing, credential providers, retries, and HTTP requests use the AWS SDK for Rust.
The guides describe the Python API and the settings rboto currently exposes.
