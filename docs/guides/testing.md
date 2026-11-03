# Testing your application

Use `rboto.testing.Stubber` to test application code without credentials or
network calls. It returns the same immutable native `Outputs` as live clients.
The service package must be installed, for example `pip install 'rboto[dynamodb]'`.

## Expected calls and responses

Pass the bound client method, a response fixture, and the exact arguments you
expect your application to use:

```python
--8<-- "examples/stubber.py"
```

The method preserves argument completion and strict type checking. Missing or
misspelled arguments and invalid nested inputs also fail at registration.
Response dictionaries are validated at runtime against the generated Rust
output contract. They are fixtures, not replacements for the typed return value.

You can use the synchronous `with` block inside an async `pytest` test. Install
`pytest` and `pytest-asyncio` and mark the test with `@pytest.mark.asyncio`.

## Fixture fields

- Use the snake_case names shown in the operation's output reference.
- Nested structures use dictionaries; tagged unions contain exactly one known
  variant, for example `{"s": "value"}` or `{"l": [{"n": "42"}]}` in DynamoDB.
- Omitted nullable fields become `None`. Nonnullable fields must be supplied,
  even if an SDK builder has a default. Unknown fields and wrong scalar types
  raise `InvalidFixtureError`.
- Enum response fields accept future strings, exposed through the same
  `UnknownEnumValue` contract as live responses. Inputs still require known
  literals.
- Registering a dictionary snapshots it. Later changes to the original fixture
  or expected arguments do not alter the expectation.

You can also pass an existing native `Output` of the correct operation. The
stubber returns that same immutable object. This preserves unknown union variants
from a captured response. Constructing `{"unknown": True}` from a dictionary is
unsupported: the SDK exposes that variant for reading but not construction.

## Matching and concurrency

Expectations match the operation and all its parameters. Different operations,
or the same operation with different arguments, can run in any order:

```python
with Stubber(client) as stub:
    stub.add_response(client.list_tables, {"table_names": ["one"]}, limit=1)
    stub.add_response(client.list_tables, {"table_names": ["two"]}, limit=2)
    two, one = await asyncio.gather(
        client.list_tables(limit=2),
        client.list_tables(limit=1),
    )
```

Calls with identical parameters consume responses in registration order. If
specific tasks need different outcomes for otherwise identical calls, serialize
those calls or use separate clients. Lists and tuples with the same input values
match equivalently; omitted optional parameters and explicit `None` match.

An unexpected call raises `StubMismatchError` without falling through to the
network. A parameter mismatch does not consume an expectation. Messages name
the operation and differing top-level fields without dumping payloads.
Normal context exit raises `UnconsumedStubsError` for unused expectations; you
can also call `stub.assert_no_pending_responses()` explicitly. If the test
already raised an exception, cleanup preserves that exception.

Activation affects only the supplied client. Enter the context before starting
tasks and await them before exiting. Overlapping stubbers on the same client
are rejected; separate clients remain independent.

## Errors, middleware and observability

`add_error` accepts `ClientError` or a modeled exception from the client's service.
Supply the error code and, optionally, request ID when testing those fields.
The stubber fills in the operation name and preserves the exception subclass.

Existing application interceptors run before the stubber and can modify the
parameters it matches. An earlier interceptor that short-circuits the call will
leave the corresponding expectation pending. Logging and OpenTelemetry observe
stubbed calls through the existing middleware path. Native metadata reports
`dispatched=False`, zero attempts and zero retries; no native client request or
credential lookup occurs.

## Streams

Response byte-stream fields accept `bytes`:

```python
from rboto import S3Client
from rboto.testing import Stubber

s3 = S3Client()
with Stubber(s3) as stub:
    stub.add_response(
        s3.get_object, {"body": b"hello"}, bucket="example", key="file.txt",
    )
    output = await s3.get_object(bucket="example", key="file.txt")
    assert await output.body.read() == b"hello"
```

Each registration creates its own consumable native stream. Register the fixture
again for another call. Existing stream objects, native Outputs containing
streams, and event-stream responses are rejected. Event-stream inputs are also
unsupported because matching must not consume application iterators.

Fixtures are bounded to 64 levels of nesting and reject cyclic containers.

## Integration tests

Stubs test application decisions and modeled shapes. They do not exercise HTTP
encoding, signing, retries, permissions, service-side constraints, or real service
behavior. Keep representative integration tests against a local endpoint or test
account.

Pass `endpoint_url` explicitly and configure test credentials in the test process:

```python
from rboto import S3Client

client = S3Client(region="us-east-1", endpoint_url="http://localhost:4566")
```

Use unique resource names and clean up in `finally` blocks. Check which operations
your emulator supports. For real-account tests, select the region, profile and
resources explicitly, assert the response, and include cleanup.
