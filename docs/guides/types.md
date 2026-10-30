# Types and responses

Each client includes method signatures and types for required arguments, nested
inputs, literal values, and response fields. No separate stub package is needed.

Import clients from `rboto` and service types from `rboto_<service>.types`:

```python
--8<-- "examples/docs/response_types.py"
```

`CreateBucketRequest` describes the request arguments; `CreateBucketOutput`
describes the returned object. Both are available in `rboto_s3.types`.
You can let the checker infer local variables such as `response`.

Every operation with a modeled return value uses `<Operation>Output`, including
streaming operations and paginator pages:

```python
from rboto_lambda.types import ListFunctionsOutput
from rboto_sqs.types import SendMessageOutput
from rboto_bedrock_runtime.types import ConverseStreamOutput, ConverseStreamEventOutput
```

`ConverseStreamOutput` describes the response dictionary; its `stream` yields
`ConverseStreamEventOutput` values. Operations without a modeled return value
return `None`.

Names such as `ListFunctionsResponse`, `SendMessageResult`, and
`ConverseStreamResponse` are not exported.

## Typed inputs

Operations use keyword arguments. Nested structures use typed dictionaries:

```python
--8<-- "examples/docs/typed_message.py"
```

An annotation helps your type checker catch a misspelled field where you construct
it. You can also pass a dictionary directly to the method. Service permissions,
resource existence, and value constraints are checked when the request runs.

Input collections accept sequences, including lists and tuples. Map inputs
require dictionaries.

## Response attributes

Regular responses and nested structures expose read-only attributes:

```python
--8<-- "examples/docs/list_buckets.py"
```

Fields may be `None` when the service omits them. Check before dereferencing them.
For an optional collection, `response.buckets or []` is useful when absence and an
empty collection mean the same thing to your application.

Native response classes, nested response classes, `ByteStream`, and event-stream
receivers are also available from the service's `types` module. These imports
refer to the same native classes returned by the client.

When a nested native class shares a name with a dictionary, its public name adds
`Output`: `Bucket` is the typed dictionary, while `BucketOutput` describes an
object from `response.buckets`. Operation output names take priority over nested
types. Further conflicts add `Value` before `Output`.

Native operation outputs are also exported from the service package root under
the same `<Operation>Output` names.

## Dictionaries

Regular outputs have `to_dict()`:

```python
--8<-- "examples/docs/response_dict.py"
```

Use it to interoperate with code expecting a dictionary. The result is
`dict[str, object]`; attributes retain more precise type information. Binary
values remain bytes, so this is not JSON serialization. Consume streaming bodies
explicitly with their stream methods.

For application-defined inputs and outputs, see
[Pydantic and dataclasses](models.md). Pydantic can validate selected response
attributes without calling `to_dict()`.

## Unions and documents

Tagged unions select exactly one variant. DynamoDB `AttributeValue` uses
`{"s": "Ada"}` for a string and `{"n": "37"}` for a number. Do not combine
variants. See [DynamoDB](dynamodb.md).

Document inputs accept JSON-like values: `None`, booleans, integers, finite floats,
strings, lists, and dictionaries with string keys. Their recursive alias is
`JSONValue` in the service's `types` module.
It is a named recursive type at runtime too: `typing.get_type_hints()` can
resolve operation annotations, and Pydantic can build a schema from the alias.

Event-stream responses use typed mappings containing async iterators. See
[Streaming](streaming.md) for their access pattern.

## Input and output types

Structures shared by requests and dictionary responses have separate types.
For example, `ToolUseBlock` describes a Bedrock input and `ToolUseBlockOutput`
describes the returned dictionary. Its optional `type` field is present but may
be `None`:

```python
from rboto_bedrock_runtime.types import ToolUseBlockOutput


def tool_kind(block: ToolUseBlockOutput) -> str:
    kind = block["type"]
    return kind.upper() if kind is not None else "unspecified"
```

Response dictionaries include known keys even when their values are absent.
Check the value against `None`; checking only that the key exists is insufficient.
Nested response structures and stream events follow the same rule.

Request unions accept only known, serializable variants. Response unions can
also contain `{"unknown": True}` when the service sends a new variant. For
example, DynamoDB uses `AttributeValue` for inputs and `AttributeValueOutput`
for returned values. Narrow or handle unknown variants before reusing a response
as a request. For pagination, use [typed paginators](pagination.md); they manage
continuation keys internally.

## Enum response values

Inputs accept the literal values defined by the installed service model.
Response types also preserve those names, including keys in attribute maps.
New enum values from the service remain ordinary strings and carry the static
type `UnknownEnumValue`; this adds no runtime wrapper.

For an explicit lookup of an attribute introduced after your installed model:

```python
from rboto import UnknownEnumValue
from rboto_sqs.types import GetQueueAttributesOutput


def future_attribute(response: GetQueueAttributesOutput) -> str | None:
    if response.attributes is None:
        return None
    return response.attributes.get(UnknownEnumValue("FutureAttribute"))
```

Known keys such as `"VisibilityTimeout"` need no wrapper. An arbitrary string
such as `"VisibilityTimout"` is a type error. `UnknownEnumValue` is a typing
marker over `str`, not a runtime class to use with `isinstance`.

## Check your code

```bash
python -m pip install mypy pyright
mypy --strict app.py
pyright app.py
```

Run the checker in the environment containing your clients. Documentation examples
are included in the repository's strict mypy and Pyright checks.
