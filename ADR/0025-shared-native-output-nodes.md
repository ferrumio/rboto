# ADR-0025: Share immutable output nodes without retaining their ancestors

- Status: Accepted
- Date: 2026-11-03

## Context

Typed getters cloned entire SDK structures on every access. Reading an SQS
message's identifier through `response.messages` therefore also cloned its
body. Lists and maps multiplied this cost even when callers never read those
large fields. The synchronous getters run on the calling Python thread.

## Decision

Generate internal Rust storage for structures with native child structures.
Move SDK fields into this storage on the Tokio worker before Python result
delivery. Each native child owns an `Arc` to its own node. Leaf structures keep
their original SDK type inside the `Arc`; outputs without native children keep
their original SDK representation. Strings, blobs, unions and primitive
collections move without an extra payload copy.

Getters create fresh Python lists, dictionaries and frozen wrappers. Nested
wrappers clone an `Arc`, not the underlying SDK structure. No mutable Python
container is cached or shared. Native object identity is not guaranteed.
Dictionary unions and explicit `to_dict()` retain their existing conversions.
Public signatures and stubs do not change.

Ownership points down the tree. A retained child owns only its subtree, not its
parent or siblings. Extract response streams before converting storage; keep
the existing single Python stream owner outside shared metadata. This also
avoids retaining consumed streams through an output ownership change.

## Tradeoffs

Preparing nodes allocates an `Arc` per native child, even when the application
never reads that child. Traversal and map rebuilding add native CPU work before
delivery. They do not acquire the GIL or create Python objects. This tradeoff
must be measured for small responses, wide collections, unused outputs and
concurrent requests, as well as large repeatedly accessed fields.

Getter container allocation, string/blob access, input conversion and
`to_dict()` remain synchronous. Native workers can contend for the GIL when
constructing Python results. Logging remains synchronous. No zero-overhead or
always-released-GIL guarantee is made.

## Alternatives

- Projected references into one shared response avoid cloning, but a tiny child
  can retain all siblings and unrelated payloads.
- Caching Python containers permits shared mutation. Copying a cached container
  still retains an eagerly cloned payload graph on first access.
- Releasing the GIL inside a getter does not free its calling event-loop thread.
- Moving all conversion to worker threads adds scheduling and lifetime costs
  without eliminating the GIL requirements of Python object access.

## Validation

The standalone generated Rust test exercises optional, boxed, list and map
children under an unregistered descriptor name. It verifies payload addresses,
thread-safe access and sibling release. Compiled response tests cover content,
independent containers, unknown enum keys, absent values, nested lifetimes,
dictionary conversion and concurrent reads. Existing compiled error,
cancellation and stream tests remain required.

See the [shared-path benchmark report](../benchmarks/output_access.md) for
release-wheel measurements and remaining costs.
