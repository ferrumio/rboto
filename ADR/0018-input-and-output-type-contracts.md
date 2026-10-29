# ADR-0018: Generate distinct input and response type contracts

- Status: Accepted
- Date: 2026-10-29
- Deciders: rboto maintainers

## Context

The same Smithy structure or union can appear in a request and a response.
Request dictionaries omit optional fields. Dictionary response conversion emits
every known field and uses `None` for absent optional values. A shared TypedDict
therefore claimed some response values could never be `None`.

Response unions also include an unknown-variant sentinel that cannot be
serialized as an input. Native enum properties lost their modeled values by
using `str`, including enum keys in response maps. Input collection conversion
already accepts sequences while the annotations required lists.

## Decision

Track input reachability and the structures returned as dictionaries separately.
Use the existing shape names for inputs. When a dictionary shape is shared,
generate a distinct response type, normally with an `Output` suffix. Preserve
the original name for response-only dictionary shapes. Allocate names against
the complete model and native class namespace.

Response dictionary keys are required because the converter always emits them.
Derive nullability from the concrete Rust field. Apply the response context
recursively to nested structures, unions, collections, and stream events.
Input unions contain only serializable variants. Output unions additionally
contain `{"unknown": Literal[True]}`.

Keep modeled input enums as Literal aliases. Response enum aliases combine the
known literals with `rboto.UnknownEnumValue`, a NewType over `str`. The native
boundary classifies a value as a modeled literal or an unknown SDK value. Both
remain ordinary Python strings: no wrapper, conversion pass, or runtime
registration is needed. This preserves future values while type checkers reject
arbitrary application strings as enum keys. Explicit future-key lookup uses
`UnknownEnumValue("FutureAttribute")`. This is a static distinction, not an
`isinstance`-checkable class.

Use `Sequence` for input lists, including nested lists. Output collections
remain lists. Map inputs remain dictionaries because native conversion requires
`PyDict`; the annotation must not promise arbitrary Mapping support.

## Consequences

- The Python annotations describe the existing native representation without
  changing ownership, runtime values, dispatch, or the native ABI.
- Unsafe response access and unsupported unknown inputs become type errors.
- A response is not automatically a valid request. Applications must narrow
  unknown variants and account for nullable fields before reusing values.
- Examples and tests cover safe response access, invalid inputs, dictionary
  keys, nested sequences, and preservation of unknown response values.
- Generic generator changes require regeneration of all services.
