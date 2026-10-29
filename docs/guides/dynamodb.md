# DynamoDB

Import `DynamoDBClient` from `rboto`. Install the service package
following [Getting started](../getting-started.md).

## Put and get an item

This example needs a table with a string partition key named `pk` and no sort key.

```python
--8<-- "examples/docs/dynamodb_items.py"
```

`AttributeValue` selects a type explicitly: `s` for strings, `n` for numbers,
`bool` for booleans, `b` for bytes, `m` for maps, and `l` for lists. Numbers are
represented as strings. rboto does not infer these types from arbitrary values.

`put_item()` replaces an existing item with the same key unless a condition rejects
the write. A missing item gives `response.item is None`.

## Query every page

```python
--8<-- "examples/docs/dynamodb_query.py"
```

The helper queries a table whose partition key is `pk`. The paginator follows
`last_evaluated_key` even when a page contains no items: filtering can remove all
items from a page. See [Pagination](pagination.md) for page size and stopping.

Conditional writes can raise `ConditionalCheckFailedError`. Batch writes require
checking `unprocessed_items`; see [Errors](errors.md#partial-failures).
