# Application services

Install the relevant client packages from the checkout; see [Getting started](../getting-started.md).
Examples use existing resources and require permission for the requested operation.

| Service | Factory | Examples |
|---|---|---|
| CloudWatch Logs | `cloudwatch_logs()` | Log groups, streams, queries, Live Tail |
| Systems Manager | `ssm()` | Parameter Store, documents, commands, automation |
| Key Management Service | `kms()` | Encryption, decryption, signatures, keys |
| Step Functions | `sfn()` | State machines and workflow executions |
| Simple Email Service v2 | `sesv2()` | Email content, templates, identities, configuration |

### CloudWatch Logs

Send events to an existing log group and stream:

```python
--8<-- "examples/docs/application_services_1.py"
```

Live Tail exposes a typed asynchronous event stream. Stream-specific failures raise
`SessionStreamingError` or `SessionTimeoutError` from `rboto_cloudwatch_logs.exceptions`.

```python
--8<-- "examples/docs/application_services_2.py"
```

### Systems Manager Parameter Store

```python
--8<-- "examples/docs/application_services_3.py"
```

Missing parameters raise `rboto_ssm.exceptions.ParameterNotFoundError`. A parameter
version or label can be appended to the name, such as `/app/config:3`.

### KMS

Binary inputs and outputs use `bytes`; rboto handles their wire encoding.

```python
--8<-- "examples/docs/application_services_4.py"
```

Supply the same encryption context when decrypting the value.

### Step Functions

Step Functions uses the `sfn` service name. Workflow inputs and outputs are JSON strings.

```python
--8<-- "examples/docs/application_services_5.py"
```

For synchronous Express executions, use `start_sync_execution()` and check `status`.
A failed workflow can return a normal API response with `status == "FAILED"` and
`error`/`cause` populated.

### SES v2

```python
--8<-- "examples/docs/application_services_6.py"
```

The sender must be an identity allowed by SES. Account sandbox and sending restrictions
still apply. Attachments and raw messages accept binary content as `bytes`.
