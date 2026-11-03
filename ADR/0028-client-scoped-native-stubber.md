# ADR-0028: Client-scoped expectations with native fixture Outputs

- Status: Accepted
- Date: 2026-11-03

## Context

Application tests need predictable responses without credentials or network calls,
while preserving the runtime identity and immutability of generated Outputs.
Global monkeypatches and dictionary replacements lose these contracts.

## Decision

Expose an opt-in `rboto.testing.Stubber`. Its registration methods use a bound
operation and `ParamSpec` to preserve static argument checking. Runtime validation
uses existing generated input annotations. A lazy private operation-name map
avoids naming heuristics. Normal imports do not load the testing module.

Generate private Output fixture factories and response conversion functions from
parsed Rust types. They validate field names, required fields and scalar shapes,
then build actual SDK values and use the same native storage as service responses.
They do not add alternate storage or checks to ordinary getters or dispatch.
Factories exist only on regular operation Outputs; fixture converters are limited
to types reachable from those Outputs.

Append a stub interceptor to the intended client's interceptor tuple for the context
lifetime. It never calls the continuation. Match by operation and normalized
parameters, consuming identical expectations in registration order. Protect
expectation mutation with a lock. Restore the original tuple on every exit and
assert unused expectations only when no other exception is propagating.

Byte-stream fixtures use bytes and receive a fresh owned stream per registration.
Reject event streams and existing consumable streams. Existing immutable native
Outputs without streams may be registered directly, including unknown union
variants. Unknown union mapping construction is rejected because non-exhaustive
SDK variants cannot be constructed externally; future enum strings remain supported.

## Consequences

Stubbed calls retain modeled errors, nested native attributes, `to_dict()` and
replacement-type validation. Native observation reports no dispatch or attempts.
Fixture creation occurs synchronously during test setup.

The native wheels include additional fixture constructors. Generated source grows,
but default Python imports, native output layouts and operation hot paths retain
their existing behavior. Stub tests complement protocol and real-service tests.

Overlapping contexts on one client are unsupported. Tasks must finish before the
context exits. Distinguishable concurrent calls match without ordering dependence;
identical calls require serialization or separate clients when outcomes are
task-specific.
