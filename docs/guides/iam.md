# AWS Identity and Access Management (IAM)

Install `rboto[iam]` and import `IAMClient` from `rboto` or `rboto_iam`.
This example lists roles under a specific IAM path:

```python
--8<-- "examples/clients/iam.py"
```

IAM is a global service in the standard AWS partition. AWS configuration still
controls endpoint and partition selection. Use `STSClient` to assume a role and
work with its temporary credentials.

IAM policy and trust policy documents are passed as JSON strings. When IAM returns
a URL-encoded policy document, decode it with `urllib.parse.unquote` before loading
it as JSON.

Role and policy changes can take time to propagate. A
[waiter](waiters.md) such as `client.waiters.role_exists` checks whether IAM can read
the role. Visibility through IAM does not guarantee that the role is ready to
assume or that its latest permissions are already effective.

See the [IAM operation reference](../reference/iam/index.md) for operation
parameters, response types, paginators, and waiters. Catch modeled errors such as
`NoSuchEntityError` from `rboto_iam.exceptions`.
