# Agent Instructions

This repository contains generated Python and Rust bindings. Before changing code, read:

1. `.ai/invariants.md`
2. `.ai/architecture.md`
3. The relevant guide under `.ai/`
4. Applicable decisions under `ADR/`

Use the role definitions in `.ai/agents/` when delegating specialized work.

## Non-negotiable rules

- Never treat generated files as the source of truth.
- Make generator changes at the descriptor, parser, generator, or template layer.
- Regenerate all services after generic codegen changes.
- Preserve one-to-one Python and PyO3 method alignment.
- Preserve async execution, stream ownership, strict typing, and modeled exceptions.
- Run the canonical checks in `.ai/commands.md` before declaring work complete.
- Keep `docs/` public-facing; internal decisions belong in `ADR/` and agent context in `.ai/`.
