# ADR-0011: Bootstrap model updates before dependency resolution

- Status: Accepted
- Date: 2026-10-27
- Deciders: rboto maintainers

## Context

The updater tried to parse crate sources before downloading them. It also updated
descriptor versions while leaving generated Cargo pins at their previous versions.
Package version changes left generated service dependencies pinned to the old core.
PRs created with the built-in automation token do not trigger ordinary PR workflows.

## Decision

Render Cargo and Python manifests from the existing templates without loading models or
parsing crates. Update these manifests before resolving and fetching dependencies, then
perform alignment and full generation. Regular generation uses the same renderers.
Service package versions and their core pins derive from the Cargo workspace version.

Use the built-in token to publish the generated branch. Create a PR when permitted; if
organization/repository policy blocks bot-created PRs, retain the branch and provide a
manual PR link. Explicitly dispatch CI, generated-source validation, and integration on
that branch, verifying its commit before dispatch. The updater never dispatches
publication, approves, merges, or changes organization policy.

Keep model updates separate from publication. An optional explicit stable `X.Y.Z` version
prepares synchronized packages and dependency pins in the update PR. Schedules preserve
the package version. A maintainer triggers the existing release workflow after merging.

## Consequences

- A fresh runner can resolve the selected crate versions before the parser needs them.
- CI validates the generated branch rather than the updater's starting main commit.
- Package preparation detects incompatible core/service version pins before building.
- Release ABI/platform builds and smoke tests remain owned by ADR-0010's release workflow.
- The updater requires `actions: write` to dispatch validation. A policy prohibiting
  bot-created PRs adds a manual PR step, while branch generation and CI remain automated.
