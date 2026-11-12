# ADR-0035: Measure application code generation with a bounded evaluation pilot

- Status: Proposed
- Date: 2026-11-08
- Deciders: rboto maintainers

## Context

Typed APIs and documentation can help coding agents, but successful examples do
not measure how often an agent writes correct code. Issue #71 requests repeatable
evaluation with behavioral checks, failures, and explicit model budgets.

## Decision

Start with five tasks under `evals/`, covering four installed services. This is
private development tooling, outside the generated clients and release catalog.
Evaluate the same dated model with API references alone and with those same
references plus existing user guides. Supply these files directly; neither
condition has tools. This pilot measures use of supplied documentation, not
discovery of documentation by a shell agent.

Require strict mypy checking and behavior through compiled bindings against
controlled HTTP peers. Validate graders using reference solutions and deliberately
incorrect implementations. Do not give either to the evaluated model. Use
randomized case values shared by each condition pair.

Run model-written code in a bounded, unprivileged container without external
network access or account credentials. The host calls the model provider;
ordinary CI runs only trusted fixture and orchestration tests. AST checks are
contract checks, not a security sandbox or a defense against grader tampering.

Pin Python and grading dependencies, record an installed SDK snapshot with file
hashes, and record the container image ID. Preparing this snapshot must not build
Rust. A source revision and package version do not attest how an existing native
library was built; retain this limitation in the manifest and published results.

Allow at most one correction per task. Require explicit live execution, model,
call budget, output-token ceiling, and admission deadline. Save every attempt and
retain failed, interrupted, and unrun cases in the denominator. Publish the small
sample and repetitions without statistical or general agent-performance claims.

## Consequences

We can identify API misunderstandings and documentation gaps without maintaining
agent-specific SDK behavior or requiring live service resources. Installed native
bindings are still required. This pilot does not test actual pip resolution,
service acceptance, runtime performance, independent tool use, or all operations.

Before expanding the task count, use observed failures to decide which
documentation or API changes merit a separate controlled experiment.

## Alternatives considered

- Twenty tasks immediately: larger implementation and model budget before checking
  whether the measurements are useful.
- Type checking alone: cannot catch early pagination termination, wrong
  acknowledgements, partial streams, or swallowed service errors.
- A public SDK stubber: unnecessary for these fixtures and would expand scope.
- Unrestricted shell agents: adds tool and environment variation to this first
  documentation comparison.

## References

- Issue #71
- `evals/README.md`
- ADR-0006: generated bindings
- ADR-0029: explicit async resource lifetimes
