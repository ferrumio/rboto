# ADR-0012: Preserve SDK capabilities with explicit service features

- Status: Accepted
- Date: 2026-10-27

## Context

The pinned service crates enable both the current HTTPS client and the legacy Hyper
0.14/Rustls 0.21 transport. rboto uses the latest behavior version, which selects the
current transport. The extra features retain an additional dependency graph.

Release wheels already enable LTO, stripping, and release optimization. Python-version
specific wheels remain a requirement; this change does not introduce abi3.

## Decision

Declare each service's Cargo features in its descriptor, preserving the upstream defaults
except the legacy HTTPS aliases. Disable implicit SDK defaults in generated manifests.
Keep modern HTTPS, Tokio, SSO, process credentials, and service-specific signing features.

During model alignment and generation, verify the descriptor against the pinned crate's
default feature set. An upstream capability change requires review rather than silently
losing support during an automated model update. Manifest bootstrapping remains independent
of downloaded crate sources.

Use Deflate level 9 for CI and release wheels. Compression changes the ZIP representation,
without changing installed native code or Python ABI tags.

Exclude Python bytecode and `__pycache__` from generated package manifests. Source-tree
imports must not add interpreter-specific caches to another interpreter's wheel. Validate
wheel contents in CI and before release smoke tests, retaining native code and typing files.

Let Cargo derive each library target name from its unique package name. A shared
`[lib] name = "_native"` causes cached builds in one target directory to package the last
service's library for other services. Python module names remain `<package>._native`
through the existing maturin configuration and PyO3 module entry point. Verify repeated
packaging from the same Cargo cache as part of validation.

## Validation

Before acceptance, compare release builds against the unchanged baseline and run canonical
quality checks, compiled method contracts, protocol/streaming tests, representative
integrations, HTTPS certificate validation, and environment/profile/process/container
credential tests.
Record actual wheel sizes and benchmark results; dependency counts do not prove size or
performance improvements.

Validation on Linux aarch64 rebuilt all 34 extensions for CPython 3.15 and four release
wheels for CPython 3.12. The compiled contract/protocol/TLS suite passed 137 tests;
representative LocalStack flows and the canonical quality checks also passed. Repackaging
the four release wheels in reverse order from the shared Cargo cache preserved each
service's native hash and passed its compiled method checks.

The four release wheels shrank by 13.2–19.8%, totaling 16.4% less compressed data and 9.9%
less native code. An HTTPS benchmark with five paired rounds, concurrency 1 and 8, and
160,000 measured calls had no errors. Small timing differences were mixed; this does not
establish a general runtime speedup. The complete release interpreter/platform matrix and
live account credential providers were not exercised locally.

## Consequences

- Removing the legacy dependency graph preserves the currently selected HTTPS transport.
- New SDK default features stop generation until their descriptor has been reviewed.
- Wheel compression can reduce downloads without changing installed native code.
- The release platform/Python matrix and separate service distributions remain unchanged.
