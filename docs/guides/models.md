# Pydantic and dataclasses

Define application inputs and outputs around an async function that calls rboto.
You choose the fields and validation rules. Client methods keep their generated
signatures, and responses keep their native, read-only attributes.

## Pydantic inputs and outputs

Install Pydantic 2 alongside the clients you use:

```bash
python -m pip install "pydantic>=2,<3"
```

rboto does not require Pydantic. The following example assumes that you have
installed the S3 client as described in [Getting started](../getting-started.md).

This tool accepts an object key and returns the object's size and content type:

```python
--8<-- "examples/docs/pydantic_tool.py"
```

The application supplies the client and bucket. The agent supplies only the
arguments described by `DocumentInput`. Pass the generated schema using your
agent framework's tool registration format; supported JSON Schema features vary
by framework and model.

`run_tool()` validates the JSON before making a request, awaits the operation,
validates the response, and returns JSON. `strict=True` disables coercions such as
converting a string to an integer. `extra="forbid"` rejects unexpected input
fields. Generated rboto method signatures still check the explicit `bucket` and
`key` arguments when you run mypy or Pyright.

`from_attributes=True` lets Pydantic read the selected attributes directly from
the native response. You do not need to call `to_dict()` first. Validation creates
a separate Pydantic model; it does not change the response. Only fields selected
by your model are requested, but reading a selected collection still materializes
that collection.

`content_length` is optional in the service response. This application's output
requires a nonnegative integer, so an omitted size causes a Pydantic
`ValidationError`. Use `int | None = None` if omission is acceptable. Defaults
apply to missing attributes; they do not replace an explicit `None`.

Service failures keep their rboto exception types. Handle those separately from
input or output validation errors. See [Errors](errors.md).

## Nested responses

Configure attribute validation on each nested model:

```python
--8<-- "examples/docs/pydantic_nested.py"
```

This reads native bucket objects inside the native response. A bucket without a
name fails validation because this application's `Bucket` requires one.
`BucketPage` describes one response page; pagination remains explicit.

rboto exposes timestamps as strings. The `creation_date` field explicitly enables
conversion to `datetime` with `Field(strict=False)`. Keep a `str` annotation if
you want to retain the original representation.

## Standard dataclasses

Dataclasses work without Pydantic. Construct them from the response attributes
you want to retain:

```python
--8<-- "examples/docs/dataclass_tool.py"
```

Standard dataclass annotations do not validate values at runtime.
`frozen=True` prevents assigning to fields after construction.

If you need JSON validation and a schema for a dataclass, add Pydantic's
`TypeAdapter`:

```python
--8<-- "examples/docs/dataclass_schema.py"
```

The adapter returns a `ToolInput` instance. Validation runs when you call the
adapter; calling the standard dataclass constructor directly bypasses it.
For native responses, construct the dataclass explicitly as above, or validate a
dictionary with an adapter. Attribute validation for `BaseModel` is a separate
path from dataclass validation.

For a complete handler that validates an event and returns JSON-compatible data,
see [AWS Lambda](aws-lambda.md).

## Structured model responses

For an agent using Bedrock, extract the JSON text produced by the model and pass
it to your application's `BaseModel.model_validate_json()` or
`TypeAdapter.validate_json()`. The Bedrock response envelope has a different
structure from that application payload.

Validate returned data even when you supplied a schema to the model. Choosing
tools, handling validation failures, and retrying model generation belong in the
application or agent framework.

## Streaming responses

Select metadata fields when validating a streaming response. Leave the body or
event receiver out of the Pydantic model, then consume it explicitly with
`await response.body.read()` or async iteration.

Validating metadata does not read the body. Streams are consumable state and do
not become JSON through model validation. See [Streaming](streaming.md).
