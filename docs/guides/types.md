# Types and responses

Each client includes method signatures and types for required arguments, nested
inputs, literal values, and response fields. No separate stub package is needed.

## Typed inputs

Operations use keyword arguments. Nested structures use typed dictionaries:

```python
--8<-- "examples/docs/typed_message.py"
```

An annotation helps your type checker catch a misspelled field where you construct
it. You can also pass a dictionary directly to the method. Service permissions,
resource existence, and value constraints are checked when the request runs.

## Response attributes

Regular responses and nested structures expose read-only attributes:

```python
--8<-- "examples/docs/list_buckets.py"
```

Fields may be `None` when the service omits them. Check before dereferencing them.
For an optional collection, `response.buckets or []` is useful when absence and an
empty collection mean the same thing to your application.

## Dictionaries

Regular outputs have `to_dict()`:

```python
--8<-- "examples/docs/response_dict.py"
```

Use it to interoperate with code expecting a dictionary. The result is
`dict[str, object]`; attributes retain more precise type information. Binary
values remain bytes, so this is not JSON serialization. Consume streaming bodies
explicitly with their stream methods.

## Unions and documents

Tagged unions select exactly one variant. DynamoDB `AttributeValue` uses
`{"s": "Ada"}` for a string and `{"n": "37"}` for a number. Do not combine
variants. See [DynamoDB](dynamodb.md).

Document inputs accept JSON-like values: `None`, booleans, integers, finite floats,
strings, lists, and dictionaries with string keys. Their recursive alias is
`JSONValue` in the service's `types` module.

Event-stream responses use typed mappings containing async iterators. See
[Streaming](streaming.md) for their access pattern.

## Check your code

```bash
python -m pip install mypy pyright
mypy --strict app.py
pyright app.py
```

Run the checker in the environment containing your clients. Documentation examples
are included in the repository's strict mypy and Pyright checks.
