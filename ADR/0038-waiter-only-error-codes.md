# ADR-0038: Validate waiter-only service error codes

- Status: Proposed
- Extends: ADR-0026

The CloudFormation model and Rust SDK waiters match the Query code
`ValidationError` even when the polling operation has no corresponding modeled
error shape. For example, the stack deletion waiter treats this code as success.

Allow a service descriptor to list exact operation and code pairs in
`[waiter_error_codes]`. Each pair must match an acceptor in a pinned Smithy
waiter and be used by a resolved waiter for an otherwise unmodeled error.
This keeps supplements from introducing arbitrary codes and detects stale
exceptions when the model changes. The runtime keeps comparing service error
codes exactly; generated operation exceptions, poll budgets and return types
continue to follow the existing contracts.
