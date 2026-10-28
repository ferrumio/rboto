# Lambda and Bedrock Runtime

These clients invoke existing functions and models. Install their service packages
as described in [Getting started](../getting-started.md).

## Invoke Lambda

Use `rboto.lambda_()`; the underscore avoids Python's `lambda` keyword.

```python
--8<-- "examples/docs/lambda_invoke.py"
```

`RequestResponse` waits for the result. Inspect `function_error` even when the
API call succeeds. The invocation payload is returned as bytes.
For event invocation, acceptance does not mean the function has completed.

## Send a Bedrock conversation

Use `rboto.bedrock_runtime()` with a model or inference-profile identifier that
supports Converse and is available to your account in the configured region.

```python
--8<-- "examples/docs/bedrock_converse.py"
```

Content blocks select a variant, here `text`. The response contains the output
and usage reported by the service. `invoke_model()` accepts encoded bytes for
model-specific payloads.

`bedrock()` manages model-related resources; `bedrock_runtime()` handles inference.
See [Streaming](streaming.md) for incremental and bidirectional operations.
