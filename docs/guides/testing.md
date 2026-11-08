# Testing your application

Use async mocks to test application decisions. Use a local endpoint or a test
account when you need to exercise request encoding and service behavior.

## Unit tests

Install `pytest` and `pytest-asyncio`, then save the example in a `test_*.py` file.

```python
--8<-- "examples/docs/mock_client.py"
```

Run async tests with your test runner's asyncio support. The mock verifies the
application's call; it does not establish that a service will accept it.

## Integration tests

Pass `endpoint_url` explicitly and configure test credentials in the test process:

```python
from rboto import S3Client

client = S3Client(region="us-east-1", endpoint_url="http://localhost:4566")
```

Use unique resource names and clean up in `finally` blocks. Check which operations
your emulator supports. For real-account tests, select the region, profile and
resources explicitly, assert the response, and include cleanup.

## What rboto's tests cover

| Check | What it establishes |
|---|---|
| Generated method contracts | Every generated Python operation has its native counterpart and forwards the expected arguments. |
| Model and type checks | Supported nested types, enums, required fields and SDK representations agree with the pinned models. |
| Compiled protocol tests | Representative requests encode correctly and responses become the expected native objects, unions and streams. |
| Failure and lifecycle tests | Modeled exceptions, interrupted streams, cancellation and resource closure retain their documented behavior. |
| Local endpoint tests | Representative service workflows work through the complete client stack. |

Method coverage does not mean that every operation has been executed against its
service. Protocol tests include JSON, Query and XML requests, nested collections,
binary values, timestamps, documents and streaming. Continue testing the
operations and service configuration that your application relies on.
