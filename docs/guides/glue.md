# AWS Glue

Import `GlueClient` from `rboto`. Install `rboto[glue]` or `rboto-glue` following
[Getting started](../getting-started.md). For development, build the service
package from this checkout.

## Browse the Data Catalog

Glue's Data Catalog describes the databases, tables, and partitions used by
services such as Athena. The client also exposes crawlers, ETL jobs, workflows,
schema registries, and the other operations in the pinned Glue model.

This executable example lists databases in the configured region:

```python
--8<-- "examples/clients/glue.py"
```

Set `AWS_REGION` and credentials with permission to read the catalog. Imports of
request and response types come from `rboto_glue.types`. Responses retain their
native immutable ownership; optional fields can be `None`.

## Pagination and long-running work

Use `client.paginators.get_tables(database_name=...)` or
`client.paginators.get_partitions(database_name=..., table_name=...)` to iterate
native response pages. Direct operations return one page. Pagination includes
continuation-token operations whose metadata is missing from the pinned model,
such as `get_catalogs`, `list_crawls`, and `query_schema_version_metadata`.
See [Pagination](pagination.md) and the [operation reference](../reference.md).

The pinned model defines no waiters. Starting a crawler or a job submits work;
it does not wait for completion. Poll `get_crawler` or `get_job_run` with a
bounded timeout, inspect the modeled state, and stop active work before deleting
resources. Closing the client releases local resources; remote work continues.
See [Closing clients](lifecycle.md).

For a finished crawl, inspect `crawler.last_crawl.status`; `READY` alone does
not establish success. Crawl-history states can include values absent from the
pinned literals. These remain available through the unknown-enum contract
described in [Types and responses](types.md).

## Errors and batch results

Catch modeled exceptions from `rboto_glue.exceptions`, such as
`EntityNotFoundError` and `AlreadyExistsError`. Batch operations can succeed at
the request level while returning per-entry failures. Inspect `errors` on
`batch_create_partition` and `batch_delete_table`, and `unprocessed_keys` on
`batch_get_partition`, before treating the workflow as successful.
