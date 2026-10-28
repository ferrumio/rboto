# Clients

The catalog contains 34 services and 1,865 operations. Install the packages your
application uses; [Getting started](getting-started.md) covers source installation.

## Imports and factories

Factories live in `rboto`. Classes live in the corresponding service package:

```python
from rboto import s3
from rboto_s3 import S3Client

first = s3(region="us-east-1")
second = S3Client(region="us-east-1")
```

Both create the same kind of client. Hyphens become underscores in Python imports.
Lambda's factory is `lambda_()`. Operations are async and use keyword arguments.

## Service catalog

| Package | Factory in `rboto` | Client class | Operations |
|---|---|---|---:|
| `rboto-api-gateway` | `api_gateway()` | `rboto_api_gateway.APIGatewayClient` | 124 |
| `rboto-bedrock-agent-runtime` | `bedrock_agent_runtime()` | `rboto_bedrock_agent_runtime.BedrockAgentRuntimeClient` | 35 |
| `rboto-bedrock-agent` | `bedrock_agent()` | `rboto_bedrock_agent.BedrockAgentClient` | 86 |
| `rboto-bedrock-agentcore-control` | `bedrock_agentcore_control()` | `rboto_bedrock_agentcore_control.BedrockAgentCoreControlClient` | 171 |
| `rboto-bedrock-agentcore` | `bedrock_agentcore()` | `rboto_bedrock_agentcore.BedrockAgentCoreClient` | 67 |
| `rboto-bedrock-data-automation-runtime` | `bedrock_data_automation_runtime()` | `rboto_bedrock_data_automation_runtime.BedrockDataAutomationRuntimeClient` | 6 |
| `rboto-bedrock-data-automation` | `bedrock_data_automation()` | `rboto_bedrock_data_automation.BedrockDataAutomationClient` | 27 |
| `rboto-bedrock-runtime` | `bedrock_runtime()` | `rboto_bedrock_runtime.BedrockRuntimeClient` | 11 |
| `rboto-bedrock` | `bedrock()` | `rboto_bedrock.BedrockClient` | 108 |
| `rboto-cloudtrail` | `cloudtrail()` | `rboto_cloudtrail.CloudTrailClient` | 60 |
| `rboto-cloudwatch-logs` | `cloudwatch_logs()` | `rboto_cloudwatch_logs.CloudWatchLogsClient` | 118 |
| `rboto-cognito-identity` | `cognito_identity()` | `rboto_cognito_identity.CognitoIdentityClient` | 23 |
| `rboto-connecthealth` | `connecthealth()` | `rboto_connecthealth.ConnectHealthClient` | 16 |
| `rboto-dynamodb` | `dynamodb()` | `rboto_dynamodb.DynamoDBClient` | 58 |
| `rboto-eventbridge` | `eventbridge()` | `rboto_eventbridge.EventBridgeClient` | 57 |
| `rboto-guardduty` | `guardduty()` | `rboto_guardduty.GuardDutyClient` | 102 |
| `rboto-kms` | `kms()` | `rboto_kms.KMSClient` | 54 |
| `rboto-lambda` | `lambda_()` | `rboto_lambda.LambdaClient` | 88 |
| `rboto-lex-runtime-v2` | `lex_runtime_v2()` | `rboto_lex_runtime_v2.LexRuntimeV2Client` | 6 |
| `rboto-pipes` | `pipes()` | `rboto_pipes.PipesClient` | 10 |
| `rboto-polly` | `polly()` | `rboto_polly.PollyClient` | 10 |
| `rboto-qbusiness` | `qbusiness()` | `rboto_qbusiness.QBusinessClient` | 83 |
| `rboto-s3` | `s3()` | `rboto_s3.S3Client` | 112 |
| `rboto-sagemaker-runtime-http2` | `sagemaker_runtime_http2()` | `rboto_sagemaker_runtime_http2.SageMakerRuntimeHTTP2Client` | 1 |
| `rboto-scheduler` | `scheduler()` | `rboto_scheduler.SchedulerClient` | 12 |
| `rboto-secrets-manager` | `secrets_manager()` | `rboto_secrets_manager.SecretsManagerClient` | 23 |
| `rboto-sesv2` | `sesv2()` | `rboto_sesv2.SESv2Client` | 116 |
| `rboto-sfn` | `sfn()` | `rboto_sfn.SFNClient` | 37 |
| `rboto-signin` | `signin()` | `rboto_signin.SignInClient` | 11 |
| `rboto-sns` | `sns()` | `rboto_sns.SNSClient` | 42 |
| `rboto-sqs` | `sqs()` | `rboto_sqs.SQSClient` | 23 |
| `rboto-ssm` | `ssm()` | `rboto_ssm.SSMClient` | 152 |
| `rboto-sts` | `sts()` | `rboto_sts.STSClient` | 11 |
| `rboto-transcribe-streaming` | `transcribe_streaming()` | `rboto_transcribe_streaming.TranscribeStreamingClient` | 5 |

Counts describe the models included in this checkout. Region availability,
permissions, and service restrictions still apply.

See [Configuration](guides/configuration.md), [Types and responses](guides/types.md),
[Async-only](guides/async-only.md), and [Errors](guides/errors.md) for shared behavior.
