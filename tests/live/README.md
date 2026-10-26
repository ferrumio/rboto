# Live AWS tests

These opt-in tests exercise rboto against AWS in `us-east-1`. They use the current AWS
credential chain and make billed model requests and Lambda invocations. Normal test runs
skip them.

```bash
RBOTO_LIVE_AWS=1 AWS_REGION=us-east-1 \
  .venv/bin/pytest tests/live/test_bedrock_lambda.py -v -s
```

Install the native `rboto-bedrock-runtime` and `rboto-lambda` packages first. AWS CLI is
required to create and delete the temporary IAM role. All Bedrock Runtime and Lambda
operations under test use rboto.

Bedrock tests cover Converse, InvokeModel, both response streaming APIs, both CountTokens
input variants, JSON tool schemas, concurrency, native outputs, and modeled errors.
Inference defaults to Nova Micro. CountTokens defaults to Claude Haiku 4.5 because Nova
Micro does not support that operation. `RBOTO_LIVE_MODEL` must use the Nova native request
format; `RBOTO_LIVE_TOKEN_MODEL` must accept the Anthropic native request format.

The Lambda test creates one uniquely named function and an execution role, then tests
invocation, errors, tags, updates, versions, aliases, and response streaming. The role has
no permission policies; the handler needs no AWS access and cannot create CloudWatch log
groups. The fixture deletes the function (including its versions and aliases) and role in
its cleanup block. A terminated process may require manual cleanup of `rboto-live-*`
resources created by that run.

The caller needs the tested Bedrock/Lambda actions, IAM CreateRole/GetRole/DeleteRole, and PassRole
for the temporary role. These tests are representative; they do not cover guardrails,
audio duplex streaming, async model jobs, or every Lambda operation.
