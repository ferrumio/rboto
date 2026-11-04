# Amazon Athena

Install the service package and import its client:

```bash
pip install "rboto[athena]"
```

```python
from rboto import AthenaClient
```

## Discover workgroups

Workgroups select query settings, including where Athena writes results. Iterate
over pages with the typed paginator:

```python
--8<-- "examples/clients/athena.py"
```

## Submit a query and retrieve results

`start_query_execution` returns a query execution ID. Awaiting this method waits
for the submission response; the SQL query continues running in Athena. Poll
`get_query_execution` until it succeeds, fails, or is cancelled, then retrieve
results with `get_query_results` or its paginator.

The following example runs a query without an input table. Set `ATHENA_WORKGROUP`
to an existing workgroup configured with an S3 results location, and set
`AWS_REGION` to its region. The configured credentials need Athena permissions
and access to that results bucket. `AWS_PROFILE` selects a named credential profile.

```python
--8<-- "examples/docs/athena.py"
```

Query results are native objects: `page.result_set.rows` contains rows, and each
row's `data` contains cells. `var_char_value` is a string or `None` for a null cell;
SQL column types are available in `result_set_metadata.column_info`. SELECT
results include a header row. Pagination returns native response pages and keeps
the continuation token internally.

The example bounds polling with a timeout and stops the query in `finally`.
The pinned Athena model does not define waiters, so this client has no generated
waiter namespace.

## Modeled errors

API failures use generated exception classes, such as
`rboto_athena.exceptions.InvalidRequestError`. A query that fails after submission
reports its failure through `query_execution.status`; inspect `state` and
`state_change_reason` when polling.

See [Configuration](configuration.md), [Pagination](pagination.md), and
[Errors](errors.md) for shared client behavior.

## Page sizes and Spark engines

Page-size limits vary by operation. `list_tags_for_resource` requires
`max_results >= 75`, while `list_engine_versions` accepts at most 10. Omit
`max_results` to use the service default. Invalid values raise a modeled
`InvalidRequestError`.

Spark features depend on the workgroup engine. The legacy `PySpark engine
version 3` rejects `monitoring_configuration` on `start_session`, although this
parameter appears in the shared API model for other engines. Check the
workgroup's engine before enabling engine-specific settings.

Cancellation states use different spellings: SQL query executions return
`CANCELLED`, while Spark calculations return `CANCELING` and `CANCELED`. Poll
the appropriate terminal state after a stop request. Explicitly terminate Spark
sessions during teardown so their compute resources are released.
