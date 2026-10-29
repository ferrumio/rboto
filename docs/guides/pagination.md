# Pagination

An operation such as `await client.list_functions()` returns one page.
Use `client.paginators.list_functions()` to request subsequent pages automatically:

```python
--8<-- "examples/docs/lambda_pagination.py"
```

Paginators accept the same keyword arguments as their client methods. Your editor
can complete the arguments and response attributes; mypy and Pyright check them.
Each iteration returns the operation's regular response, including its metadata.
You do not need to import request types or convert continuation tokens.

## Page size and stopping

Use the operation's own page-size argument, such as Lambda's `max_items`,
DynamoDB's `limit`, or S3's `max_keys`. It controls each request, not the total
number of results returned by the iterator. The service may return fewer items.

Requests start when iteration starts. There is no prefetch: the next request
starts when you ask for the next page. Use `break` to stop making requests.
An empty page does not stop pagination if the service supplies a continuation
token. Pagination ends when no token remains, the service indicates completion,
or the next token repeats the previous one.

You can supply a starting token using the operation's ordinary argument, for
example `marker` on `list_functions()`. Paginators handle subsequent tokens.
There is no separate resume-token format or total-item limit.

## DynamoDB

```python
--8<-- "examples/docs/dynamodb_query.py"
```

This queries a table with a string partition key named `pk`. Filtering can leave
a page empty while more pages remain. The paginator follows `last_evaluated_key`
internally; application code only handles the returned items.

## Supported operations

Only operations with validated pagination metadata appear under
`client.paginators`. Each service package's README marks these operations next
to their signatures. Clients without supported paginated operations do not
expose `paginators`.

Each page uses the normal client operation, including its credentials,
configuration, logging, interceptors, and modeled exceptions. A request failure
propagates to the caller; pagination does not add a second retry policy.
