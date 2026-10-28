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
from rboto import s3

client = s3(region="us-east-1", endpoint_url="http://localhost:4566")
```

Use unique resource names and clean up in `finally` blocks. Check which operations
your emulator supports. For real-account tests, select the region, profile and
resources explicitly, assert the response, and include cleanup.
