# ADR-0012: Generate a standalone Rust CLI

- Status: Accepted
- Date: 2026-10-27
- Deciders: rboto maintainers

## Context

The CLI must expose the entire rboto catalog in one executable without installing
Python or separate client packages. The earlier unified native Python experiment
made local compilation too expensive.

## Decision

Use a separate Cargo workspace under `cli/`. Generate one adapter per descriptor
from the existing Smithy alignment and Rust parser, plus the command catalog and
dispatch. The handwritten core owns JSON conversion primitives, event input,
atomic file output and error normalization; the app owns argument parsing.
Reuse the Rust SDK directly without linking PyO3.

Derive the CLI version and shared service pins from the root workspace. Descriptor
`cli_features` preserves each pinned SDK's default capabilities while selecting its
current transport instead of both current and legacy HTTPS transports. Generation
fails if the pinned crate's feature defaults drift from the descriptor selection.
This does not change the Python extension feature policy.

`generate --all` regenerates both products. Manifest bootstrap, model updates and
version checks cover both workspaces and lockfiles. CI compiles every adapter and
exercises the executable against local protocol fixtures.

Use release optimization level 2, 16 codegen units, no LTO, no debug information
and stripped symbols. Keep local builds to one Cargo job, with explicit CPU,
memory, elapsed-time and cache limits. Do not expand build matrices or enable
whole-program optimization without measurements.

## Consequences

- One executable includes every modeled operation supported by rboto.
- The build does not require Python extensions or their link configuration.
- The CLI adds generated review artifacts and a separate dependency lockfile.
- Shared model changes must preserve both Python and CLI generation.
- CLI inputs use strict JSON shapes; high-level transfer orchestration such as
  `s3 sync` remains separate future work.
- Host compilation and local fixtures cannot establish live-service coverage for
  all operations or binary compatibility with other platforms.
