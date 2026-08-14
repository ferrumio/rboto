# AI Engineering Context

This directory gives coding agents concise, repository-specific guidance. It is internal
engineering context, not public product documentation.

Read in this order:

1. `../AGENTS.md`
2. `invariants.md`
3. `architecture.md`
4. The relevant topic file and agent definition
5. Applicable records in `../ADR/`

## Topic guides

- `architecture.md`: package and runtime boundaries.
- `invariants.md`: rules that changes must preserve.
- `commands.md`: canonical generation and verification commands.
- `codegen.md`: source-of-truth and regeneration workflow.
- `testing.md`: test layers and LocalStack expectations.
- `ci-release.md`: routine CI versus distribution builds.

## Agent definitions

- `agents/codegen-agent.md`
- `agents/rust-bridge-agent.md`
- `agents/python-api-agent.md`
- `agents/test-agent.md`
- `agents/reviewer-agent.md`
