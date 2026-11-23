# Clients

Import client classes directly from `rboto`:

```python
from rboto import DynamoDBClient, S3Client

objects = S3Client(region="us-east-1")
items = DynamoDBClient(region="us-east-1")
```

The same class is used for construction and type annotations. Operations are
async and use keyword arguments. Importing a class loads only its service
package; constructing it does not send a network request.

## Install the services you use

The catalog contains 41 services and 2,725 operations. Each service has its own
package. `rboto[s3]` selects the matching `rboto-s3` dependency once packages are
published. [Getting started](getting-started.md) covers source installation.

`import rboto` works without service packages. `from rboto import S3Client`
requires `rboto-s3` and raises an `ImportError` with installation guidance if
that package is missing. A broken native extension or another missing dependency
keeps its original import error.

Use explicit imports. `from rboto import *` requests every exported client and
therefore requires all service packages.

## Client catalog

All classes in this table are available from `rboto`:

| Client class | Service package | Operations |
|---|---|---:|
| `APIGatewayClient` | `rboto-api-gateway` | 124 |
| `AthenaClient` | `rboto-athena` | 75 |
| `BedrockAgentClient` | `rboto-bedrock-agent` | 86 |
| `BedrockAgentCoreClient` | `rboto-bedrock-agentcore` | 75 |
| `BedrockAgentCoreControlClient` | `rboto-bedrock-agentcore-control` | 175 |
| `BedrockAgentRuntimeClient` | `rboto-bedrock-agent-runtime` | 35 |
| `BedrockClient` | `rboto-bedrock` | 133 |
| `BedrockDataAutomationClient` | `rboto-bedrock-data-automation` | 27 |
| `BedrockDataAutomationRuntimeClient` | `rboto-bedrock-data-automation-runtime` | 6 |
| `BedrockRuntimeClient` | `rboto-bedrock-runtime` | 11 |
| `CloudFormationClient` | `rboto-cloudformation` | 92 |
| `CloudTrailClient` | `rboto-cloudtrail` | 60 |
| `CloudWatchClient` | `rboto-cloudwatch` | 59 |
| `CloudWatchLogsClient` | `rboto-cloudwatch-logs` | 118 |
| `CognitoIdentityClient` | `rboto-cognito-identity` | 23 |
| `ConnectHealthClient` | `rboto-connecthealth` | 23 |
| `DynamoDBClient` | `rboto-dynamodb` | 62 |
| `EventBridgeClient` | `rboto-eventbridge` | 57 |
| `EventBridgePipesClient` | `rboto-pipes` | 10 |
| `EventBridgeSchedulerClient` | `rboto-scheduler` | 12 |
| `FirehoseClient` | `rboto-firehose` | 12 |
| `GlueClient` | `rboto-glue` | 312 |
| `GuardDutyClient` | `rboto-guardduty` | 103 |
| `IAMClient` | `rboto-iam` | 181 |
| `KMSClient` | `rboto-kms` | 56 |
| `KinesisClient` | `rboto-kinesis` | 57 |
| `LambdaClient` | `rboto-lambda` | 91 |
| `LexRuntimeV2Client` | `rboto-lex-runtime-v2` | 6 |
| `PollyClient` | `rboto-polly` | 16 |
| `QBusinessClient` | `rboto-qbusiness` | 83 |
| `S3Client` | `rboto-s3` | 112 |
| `SESV2Client` | `rboto-sesv2` | 117 |
| `SNSClient` | `rboto-sns` | 42 |
| `SQSClient` | `rboto-sqs` | 23 |
| `SSMClient` | `rboto-ssm` | 154 |
| `STSClient` | `rboto-sts` | 12 |
| `SageMakerRuntimeHTTP2Client` | `rboto-sagemaker-runtime-http2` | 1 |
| `SecretsManagerClient` | `rboto-secrets-manager` | 26 |
| `SignInClient` | `rboto-signin` | 16 |
| `StepFunctionsClient` | `rboto-sfn` | 37 |
| `TranscribeStreamingClient` | `rboto-transcribe-streaming` | 5 |

Counts describe the models included in this checkout. Region availability,
permissions, and service restrictions still apply.

## Share configuration

Classes accept the same `Session` settings as the compatibility factories:

```python
from rboto import S3Client, Session, SQSClient

session = Session(region="us-east-1", profile="development")
objects = S3Client(session=session)
messages = SQSClient(session=session)
```

Use either `session` or explicit constructor settings. Combining them raises
`ValueError`. See [Configuration](guides/configuration.md).

## Existing imports

Service-package imports continue to work. `from rboto_s3 import S3Client` and
`from rboto import S3Client` refer to the same class.

These older names remain aliases in their service packages:

| Existing import | Preferred import |
|---|---|
| `from rboto_sfn import SFNClient` | `from rboto import StepFunctionsClient` |
| `from rboto_sesv2 import SESv2Client` | `from rboto import SESV2Client` |
| `from rboto_pipes import PipesClient` | `from rboto import EventBridgePipesClient` |
| `from rboto_scheduler import SchedulerClient` | `from rboto import EventBridgeSchedulerClient` |

Factories such as `rboto.s3()`, `rboto.sfn()` and `rboto.lambda_()` also remain
available. They construct the same classes and accept the same settings.
Package names and extras are unchanged.

Input types, response classes, and modeled exceptions stay in the service
packages, for example `rboto_s3.GetObjectOutput` and
`rboto_s3.exceptions.NoSuchKeyError`.

See [Types and responses](guides/types.md), [Async-only](guides/async-only.md),
and [Errors](guides/errors.md) for shared behavior.
