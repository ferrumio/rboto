# AWS Lambda

Use rboto inside a Python Lambda function with a synchronous handler that runs
your async code. Install the clients your function calls: a function accessing
S3 needs `rboto` and `rboto-s3`. The `rboto-lambda` package is needed only when
your code calls the Lambda service API.

## A typed handler

This example receives an object key, reads its metadata from S3, and returns
validated JSON data. It uses the optional Pydantic integration described in
[Pydantic and dataclasses](models.md).

Save it as `lambda_function.py`:

```python
--8<-- "examples/docs/lambda_function.py"
```

Configure the function with:

| Setting | Value for this example |
|---|---|
| Runtime | Python 3.12 |
| Handler | `lambda_function.lambda_handler` |
| Environment variable | `DOCUMENT_BUCKET`, set to your bucket name |
| Execution role permissions | `s3:GetObject` on the objects the function reads |

Invoke it with a JSON object:

```json
{"key": "reports/latest.pdf"}
```

For a 4096-byte PDF, the response would be:

```json
{"content_length": 4096, "content_type": "application/pdf"}
```

The [Python Lambda handler](https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html)
must be a regular `def`. `asyncio.run()` creates an event loop for the invocation,
waits for the async work, and closes that loop before the handler returns.
The rboto operation remains async.

Return `model_dump(mode="json")` so Lambda receives JSON-compatible Python
values. Returning `model_dump_json()` would return a string containing JSON.
Raw rboto response objects and Pydantic model instances are not handler return
values that Lambda can serialize directly.

This event format is for a direct invocation. API Gateway, SQS, and other event
sources have their own envelopes. Extract and validate the payload from those
envelopes, and return the response format required by the integration.

## Reuse the client

Create the client outside the handler. Lambda can reuse the execution environment
for subsequent invocations, and the client keeps its native SDK configuration and
connections. Each invocation in the example gets its own Python event loop.
Keep per-invocation inputs and results local to the handler.

To log operations with the invocation's request ID, see the
[logging example for Lambda](logging.md#aws-lambda). It uses rboto's built-in
JSON output and keeps correlation local to each invocation.

The default credential chain uses the temporary credentials provided for the
function's execution role and the region supplied by Lambda. A local development
profile or access keys embedded in the deployment package are unnecessary.

The role needs permission for the operations you call. For this example,
[`HeadObject` requires `s3:GetObject`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html)
on the target objects.

## Package the function

rboto service packages contain native extensions. Match all three properties of
the function:

| Function property | Required wheel |
|---|---|
| Python 3.12 | CPython 3.12 (`cp312`), not a wheel for another Python version |
| `arm64` | Linux `aarch64` |
| `x86_64` | Linux `x86_64` |

Use Lambda-compatible manylinux wheels, not macOS wheels or binaries copied
from an arbitrary Linux virtual environment. Pydantic also has a native
dependency, so apply the same target selection to all dependencies.

rboto packages are not published on PyPI yet. Before running the commands below,
place matching **release-built** `rboto` and `rboto-s3` wheels from the same
version in `wheelhouse/`. The project's release workflow produces manylinux
wheels. See [Getting started](../getting-started.md) for the package layout.

From the repository root, this packages version `0.1.0` for Python 3.12 on
`arm64`. Use a fresh `build/lambda` directory for each package:

```bash
python -m pip install \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.12 \
  --abi cp312 \
  --only-binary=:all: \
  --find-links wheelhouse \
  --target build/lambda \
  "rboto==0.1.0" "rboto-s3==0.1.0" "pydantic>=2,<3"

cp examples/docs/lambda_function.py build/lambda/lambda_function.py
cd build/lambda
zip -r ../function.zip .
```

For an `x86_64` function, use `--platform manylinux2014_x86_64`. Update both
rboto version pins together when using another release.

The handler file and installed packages must be at the root of the ZIP. Upload
`build/function.zip`, then set the handler and `DOCUMENT_BUCKET` as shown above.
The packaging command expects the rboto wheels to exist; it does not compile
them.

Include only the service clients your function uses. Lambda's ZIP deployment
limit is 250 MB uncompressed, including layers. A layer can share dependencies
between functions but still needs matching Python and architecture.
See the [Lambda Python packaging guide](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html)
for deployment and layer requirements.

## Complete work before returning

Await every operation needed by the invocation. For concurrent operations, use
structured concurrency such as `asyncio.TaskGroup` and wait for the group before
returning. Work left in background tasks is not a reliable continuation of a
Lambda invocation.

Consume any required byte streams or event streams inside the async function.
Return their processed data, not the stream object.

Set the Lambda timeout to cover the work, and use an
[asyncio deadline](async-only.md#deadlines) to stop waiting before the invocation
runs out of time. Invalid input in this example raises `ValidationError`; service
failures keep their rboto exception types. Letting them propagate marks the
invocation as failed. Retry behavior depends on the event source, so operations
that write data should account for repeated delivery.
