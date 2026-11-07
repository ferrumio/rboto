# ADR-0033: Validate model and SDK semantics before generation

- Status: Accepted
- Date: 2026-11-07
- Extends: ADR-0002, ADR-0006, ADR-0018

## Decision

Generation and the alignment report must reject incompatible model/crate pairs,
including member-only drift, before writing service artifacts. Operation surface
checks alone are insufficient.

Parse the pinned SDK's declaration subset using tokens and balanced delimiters.
Ignore comments and attributes without depending on line breaks, indentation or
trailing generic commas. Normalize supported type paths and generic arguments.
Reject missing source files, unsupported public declarations, incomplete enum
string mappings and missing builders with source paths in diagnostics. This is
not a general Rust parser: an unfamiliar syntax requires an explicit parser
change and a fixture.

Compare operation fields and nested structures, primitive widths, collection
targets, sparse values, enum wire values, union payloads, streaming types and
modeled error fields. Recursive Box wrappers preserve their target semantics.
Builder-required fields are extracted only from the build method body, and
compared with modeled requiredness, defaults and clientOptional.

Preserve the SDK's intentional client representations:

- Required operation members can remain optional in lenient request/response
  structures. Response nullability continues to follow Rust, as in ADR-0018.
- Required nested structures/unions can be nullable, including recursive nodes.
- Defaults and clientOptional affect nested nullability and builder requirements.
- Stream owners need not be optional even if their modeled member is optional.
- SDK errors can include a common message absent from the model.
- Unit structures, unknown union/enum variants and event error aggregates have
  explicit representation rules.

Service-specific differences belong in descriptors, with exact types and a
reason. S3 declares its parsed Expires timestamp and additional raw header
fields. Missing, changed or unused exceptions fail validation; an exception is
not a blanket permission to ignore a field or shape.

## Validation boundaries

The exhaustive method suite checks registration, signatures and Python argument
forwarding. It does not serialize every native operation or contact every service.
Controlled HTTP peers test representative compiled converters and protocols,
including JSON unions/binary values, Query lists, XML timestamps, sparse and
recursive structures, documents, stream ownership and modeled errors.
LocalStack tests cover additional service flows. Neither replaces real-service
compatibility testing.

Parser fixtures include supported formatting changes and unsupported syntax.
Semantic mutation tests must reject drift at the generator entry point, before
any output file is written. Regenerate every registered service twice and compare
the result with committed artifacts in CI to guard deterministic generation.
