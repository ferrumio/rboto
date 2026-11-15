# ADR-0035: Generate closed typed dictionary union variants

- Status: Accepted
- Date: 2026-11-15
- Extends: ADR-0018

## Context

Modeled unions contain exactly one variant at runtime. Their open TypedDict
definitions permit structural subtypes with additional keys, so a key-presence
check does not reliably narrow the union in strict mypy. Consumers must write
type guards despite the native converter already enforcing the variant rule.

This applies to regular and streaming unions across services. Changing the
response representation would be a separate API decision.

## Decision

Generate each modeled input and output union variant with `closed=True`, using
`typing_extensions.TypedDict`. Close the output-only unknown sentinel as well.
Preserve required keys, recursive annotations, public names, and input/output
separation. Ordinary structures remain open typed dictionaries; maps and JSON
documents retain arbitrary keys.

Each service package directly depends on `typing-extensions>=4.15,<5`.
Document mypy 2.2 and Pyright 1.1.414 as the minimum supported checker versions
for this contract. Type checkers remain development tools, not runtime
dependencies.

Pyright 1.1.400 passed the basic narrowing example but did not reject the
structural-extra and subclass-extension cases. The supported baseline must
pass the negative contracts as well as the positive examples.

Generated references preserve closedness and show key-based narrowing using
the actual public union and field names. Keep examples on the website and use
the existing short reference links in packaged docstrings.

The runtime representation remains a dictionary. This change adds no native
validation pass, discriminator field, helper function, or wrapper class.
Existing native union conversion still enforces exactly one known input variant.

## Compatibility

Closed TypedDicts are more restrictive under structural subtyping. A separate
open TypedDict or a subtype with additional keys may no longer be assignable to
a variant. Consumers should use the exported input types or construct the
dictionary in the operation's typed argument context. A variant cannot be
extended with new keys.

Future output variants keep the existing unknown sentinel. Closing an output
variant does not make an unknown response acceptable as an input.

## Validation

Regenerate the complete catalog. Consumer tests exercise every union variant
with strict mypy and Pyright, and compile the generated reference examples.
Negative tests reject mixed variants, invalid values, unknown inputs, unsafe
access, and structural extras. Test generated declarations under supported
Python versions and verify the lower dependency/checker bounds.

## References

- Issue #107
- ADR-0018, ADR-0023, ADR-0034
- PEP 728
