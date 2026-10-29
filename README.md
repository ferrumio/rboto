# rboto

[![CI](https://github.com/ferrumio/rboto/actions/workflows/ci.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/ci.yml)
[![Integration](https://github.com/ferrumio/rboto/actions/workflows/integration.yml/badge.svg)](https://github.com/ferrumio/rboto/actions/workflows/integration.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ferrumio/rboto/badge)](https://scorecard.dev/viewer/?uri=github.com/ferrumio/rboto)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](https://www.python.org/)
[![Typing](https://img.shields.io/badge/typing-strict-blue)](docs/clients.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

**Async-only, typed Python clients for AWS services, powered by Rust.**

rboto has clients for 34 services, including S3, DynamoDB, Lambda, Bedrock,
EventBridge, and SQS. Install the services you use, import their client classes
from `rboto`, and await operations. Requests run through the AWS SDK for Rust;
Python inputs and response attributes carry types for your editor and type checker.

rboto is an independent project maintained by Ferrum. It is not affiliated with,
sponsored by, or endorsed by Amazon Web Services (AWS).

**Preview (Alpha).** rboto is under active development. APIs may change between releases.

[Documentation](https://ferrumio.github.io/rboto/) ·
[Client catalog](docs/clients.md) ·
[Examples](examples/clients/) ·
[Issues](https://github.com/ferrumio/rboto/issues)

## Install a client

rboto packages are not published on PyPI yet. To try the project, install from a
checkout with CPython 3.12–3.15, Rust 1.94.1 or newer, a C compiler, and CMake:

```bash
git clone https://github.com/ferrumio/rboto.git
cd rboto
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./packages/rboto ./packages/rboto-sts
```

This builds only the STS client used below. Install additional service directories
from the same checkout when you need them:

```bash
python -m pip install ./packages/rboto ./packages/rboto-s3 ./packages/rboto-dynamodb
```

For published releases, the equivalent installation uses extras:

```bash
python -m pip install "rboto[s3,dynamodb]"
```

Each extra selects a service package at the matching rboto version. Installing
`rboto` alone gives you shared configuration and utilities; it installs no
service clients. `rboto[all]` selects every service.

See [getting started](docs/getting-started.md) for credentials and the Linux and
macOS wheel targets.

## Make your first request

Configure credentials through your environment, an existing shared profile, or
your workload's role. Save this as `identity.py`:

```python
import asyncio

from rboto import STSClient


async def main() -> None:
    client = STSClient(region="us-east-1")
    response = await client.get_caller_identity()
    print(response.account)
    print(response.arn)


if __name__ == "__main__":
    asyncio.run(main())
```

Run `python identity.py` to print the account ID and principal ARN associated
with those credentials.

Client construction is synchronous and sends no request. **Every service
operation is asynchronous.** In a script, start the event loop with
`asyncio.run()` as above. Inside an async application or notebook, await the
operation directly. Reuse clients across calls.

Independent requests can run concurrently with `asyncio.gather()`. For larger
workloads, limit the number of requests in flight. See the
[async guide](docs/guides/async-only.md) for concurrency and deadlines.

## Typed inputs and responses

Methods take keyword arguments. Nested inputs use `TypedDict`, literals, and
tagged unions from the service package:

```python
from rboto import SQSClient
from rboto_sqs.types import MessageAttributeValue


async def send_message(client: SQSClient, queue_url: str) -> str | None:
    source: MessageAttributeValue = {"data_type": "String", "string_value": "orders"}
    response = await client.send_message(
        queue_url=queue_url,
        message_body="Order received",
        message_attributes={"source": source},
    )
    return response.message_id
```

Regular responses expose read-only attributes such as `response.message_id`.
Optional fields may be `None`; nested responses also have typed attributes.
Use `response.to_dict()` when another library needs a dictionary.

Clients include `py.typed` markers and native type stubs. No separate typing
package is needed. With your service packages installed, check application code
with `mypy --strict` or Pyright. The repository checks its client APIs and examples
with both. Static typing does not replace service-side validation.

See [types and responses](docs/guides/types.md) for unions and streaming outputs,
or [Pydantic and dataclasses](docs/guides/models.md) for application-defined models.

## Choose your clients

All classes below are imported from `rboto`. Each package README contains a usage
example, its complete operation catalog, and Python method signatures.

| Service | Client class | Package and operations |
|---|---|---|
| API Gateway | `APIGatewayClient` | [rboto-api-gateway](packages/rboto-api-gateway/README.md) |
| Bedrock | `BedrockClient` | [rboto-bedrock](packages/rboto-bedrock/README.md) |
| Bedrock Agents | `BedrockAgentClient` | [rboto-bedrock-agent](packages/rboto-bedrock-agent/README.md) |
| Bedrock Agent Runtime | `BedrockAgentRuntimeClient` | [rboto-bedrock-agent-runtime](packages/rboto-bedrock-agent-runtime/README.md) |
| Bedrock AgentCore | `BedrockAgentCoreClient` | [rboto-bedrock-agentcore](packages/rboto-bedrock-agentcore/README.md) |
| Bedrock AgentCore Control | `BedrockAgentCoreControlClient` | [rboto-bedrock-agentcore-control](packages/rboto-bedrock-agentcore-control/README.md) |
| Bedrock Data Automation | `BedrockDataAutomationClient` | [rboto-bedrock-data-automation](packages/rboto-bedrock-data-automation/README.md) |
| Bedrock Data Automation Runtime | `BedrockDataAutomationRuntimeClient` | [rboto-bedrock-data-automation-runtime](packages/rboto-bedrock-data-automation-runtime/README.md) |
| Bedrock Runtime | `BedrockRuntimeClient` | [rboto-bedrock-runtime](packages/rboto-bedrock-runtime/README.md) |
| CloudTrail | `CloudTrailClient` | [rboto-cloudtrail](packages/rboto-cloudtrail/README.md) |
| CloudWatch Logs | `CloudWatchLogsClient` | [rboto-cloudwatch-logs](packages/rboto-cloudwatch-logs/README.md) |
| Cognito Identity | `CognitoIdentityClient` | [rboto-cognito-identity](packages/rboto-cognito-identity/README.md) |
| Connect Health | `ConnectHealthClient` | [rboto-connecthealth](packages/rboto-connecthealth/README.md) |
| DynamoDB | `DynamoDBClient` | [rboto-dynamodb](packages/rboto-dynamodb/README.md) |
| EventBridge | `EventBridgeClient` | [rboto-eventbridge](packages/rboto-eventbridge/README.md) |
| EventBridge Pipes | `EventBridgePipesClient` | [rboto-pipes](packages/rboto-pipes/README.md) |
| EventBridge Scheduler | `EventBridgeSchedulerClient` | [rboto-scheduler](packages/rboto-scheduler/README.md) |
| GuardDuty | `GuardDutyClient` | [rboto-guardduty](packages/rboto-guardduty/README.md) |
| KMS | `KMSClient` | [rboto-kms](packages/rboto-kms/README.md) |
| Lambda | `LambdaClient` | [rboto-lambda](packages/rboto-lambda/README.md) |
| Lex Runtime V2 | `LexRuntimeV2Client` | [rboto-lex-runtime-v2](packages/rboto-lex-runtime-v2/README.md) |
| Polly | `PollyClient` | [rboto-polly](packages/rboto-polly/README.md) |
| Q Business | `QBusinessClient` | [rboto-qbusiness](packages/rboto-qbusiness/README.md) |
| S3 | `S3Client` | [rboto-s3](packages/rboto-s3/README.md) |
| SageMaker Runtime HTTP/2 | `SageMakerRuntimeHTTP2Client` | [rboto-sagemaker-runtime-http2](packages/rboto-sagemaker-runtime-http2/README.md) |
| Secrets Manager | `SecretsManagerClient` | [rboto-secrets-manager](packages/rboto-secrets-manager/README.md) |
| SES V2 | `SESV2Client` | [rboto-sesv2](packages/rboto-sesv2/README.md) |
| SNS | `SNSClient` | [rboto-sns](packages/rboto-sns/README.md) |
| SQS | `SQSClient` | [rboto-sqs](packages/rboto-sqs/README.md) |
| SSM / Parameter Store | `SSMClient` | [rboto-ssm](packages/rboto-ssm/README.md) |
| STS | `STSClient` | [rboto-sts](packages/rboto-sts/README.md) |
| Sign-In | `SignInClient` | [rboto-signin](packages/rboto-signin/README.md) |
| Step Functions | `StepFunctionsClient` | [rboto-sfn](packages/rboto-sfn/README.md) |
| Transcribe Streaming | `TranscribeStreamingClient` | [rboto-transcribe-streaming](packages/rboto-transcribe-streaming/README.md) |

Importing a client requires its service package. If `rboto-s3` is missing,
`from rboto import S3Client` raises an `ImportError` with installation guidance.
`import rboto` works without any service packages.

## Configure and reuse clients

A `Session` shares settings between clients. Use `ClientConfig` for retries,
timeouts, explicit credentials, roles, proxies, or additional TLS certificates:

```python
from rboto import ClientConfig, DynamoDBClient, S3Client, Session

session = Session(
    region="us-east-1",
    profile="development",
    config=ClientConfig(max_attempts=3, operation_timeout=10.0),
)
objects = S3Client(session=session)
items = DynamoDBClient(session=session)
```

The profile must already be configured. Omit `profile` to use the default
credential chain. Sessions share settings; each client keeps its own native
client and connections. See [configuration](docs/guides/configuration.md) for
SSO, workload roles, credential refresh, and endpoint overrides.

## Enable operation logs

Call `configure_logging()` once for JSON logs across all clients. Each service
has its own logger name, such as `rboto.s3`, and can be configured separately:

```python
from rboto import configure_logging

configure_logging(
    level="INFO",
    service_levels={"s3": "OFF", "dynamodb": "DEBUG"},
)
```

Logging is opt-in. See [logging](docs/guides/logging.md) for fields, correlation
IDs, and output configuration.

## More examples

| Task | Guide |
|---|---|
| Read or upload objects and paginate S3 results | [S3](docs/guides/s3.md) |
| Read and write DynamoDB items | [DynamoDB](docs/guides/dynamodb.md) |
| Invoke Lambda functions or Bedrock models | [Lambda and Bedrock](docs/guides/inference.md) |
| Publish events, configure pipes, and create schedules | [Events and schedules](docs/guides/events.md) |
| Consume byte streams and event streams | [Streaming](docs/guides/streaming.md) |
| Catch service exceptions and handle partial failures | [Errors](docs/guides/errors.md) |
| Add caching, change parameters, or wrap operations | [Interceptors](docs/guides/interceptors.md) |
| Use rboto inside a Lambda handler | [AWS Lambda](docs/guides/aws-lambda.md) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), [GOVERNANCE.md](GOVERNANCE.md),
[SECURITY.md](SECURITY.md), and the [Code of conduct](CODE_OF_CONDUCT.md).
The [codegen guide](codegen/README.md) covers client generation;
[.ai/commands.md](.ai/commands.md) lists the verification commands.

## License

[Apache-2.0](LICENSE).
