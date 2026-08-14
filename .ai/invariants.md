# Engineering Invariants

Every change must preserve these rules unless a new ADR explicitly changes one.

1. Do not fix generated code by editing generated files directly.
2. Generic behavior belongs in the generator or templates.
3. Service differences are declared in service TOML where practical.
4. Regenerate all services after a generic codegen change.
5. Every generated Python client method has exactly one same-named PyO3 method.
6. Python methods are async and keyword-only.
7. Inputs remain strictly typed mappings, literals, and tagged unions.
8. Regular outputs remain frozen Rust-owned objects with lazy properties.
9. `to_dict()` is an interoperability path, not the primary output API.
10. Byte streams move once into Python ownership and are never fake-cloned.
11. Event streams may use specialized wrappers when regular ownership is unsafe.
12. Modeled SDK errors become generated Python exception subclasses.
13. LocalStack coverage is representative; method-contract coverage is exhaustive.
14. Routine CI is not evidence of the complete release platform matrix.
15. Never commit credentials, private endpoints, generated wheels, or build artifacts.
