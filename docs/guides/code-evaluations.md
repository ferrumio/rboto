# Code generation evaluations

rboto evaluates whether a model can use its documentation to write application
code that passes strict type checks and behaves correctly. A solution must
handle the task's error paths and resource lifetimes as well as its successful
requests.

If you use a coding agent, give it the relevant [operation reference](../reference.md)
and the guides for [closing resources](lifecycle.md), [types](types.md), and the
workflow you are implementing. The pilot below records what happened when the
same model received references alone or references with those guides.

## What the pilot checks

The five tasks cover:

- S3 bucket outputs projected into Pydantic models, including absent fields.
- DynamoDB pagination through an empty intermediate page.
- SQS deletion only after the message handler succeeds.
- S3 streaming with complete content, modeled errors, and resource closure.
- Bedrock nested inputs and response union variants.

Each generated solution is checked for its declared dependencies and API
constraints, checked with `mypy --strict`, then executed against controlled HTTP
responses through the compiled rboto clients. Reference solutions and
deliberately incorrect implementations test the graders themselves.

For example, the graders detect an invented `client.close()` method, stopping
pagination at an empty page, deleting a message before processing finishes, and
leaving a client open when an operation raises. Passing the type checker alone
does not establish that these behaviors are correct.

## Where inference happens

**The evaluation checks in CI do not call a model.** They run trusted reference
solutions and deliberate mistakes against local endpoints.

| Execution | Model inference | Requests from the evaluated application |
|---|---|---|
| CI fixture and runner tests | None | Controlled local HTTP endpoints |
| `run` without `--live` | None; prints the plan | Application code is not executed |
| `run --live` | Bedrock generates code, using the host's credentials | Controlled HTTP endpoints inside an isolated container |
| `regrade` | None; reuses stored code | Controlled HTTP endpoints inside an isolated container |

In live mode, the model receives the task and documentation and returns Python
code. It has no shell, browser, or filesystem tools. The generated code runs in
a separate container with external networking disabled and fake credentials.
Even the Bedrock application task receives controlled responses; the live
inference call belongs to the code-generation step.

## Recorded results

The 2026-11-08 pilot used Claude Haiku 4.5
(`eu.anthropic.claude-haiku-4-5-20251001-v1:0`), temperature 0, and two repetitions
of each task per condition. Both conditions used the same model and SDK
snapshot; the added input was the user guides. The SDK was a Linux aarch64
development snapshot at `e4ec3b58`, carrying version 0.2.0, rather than the
published 0.2.0 wheels.

| First-attempt assessment | References only | References + guides |
|---|---:|---:|
| Original checks: dependencies, typing, and behavior | 0/10 | 3/10 |
| Diagnostic replay with canonical dependency declarations | 0/10 | 7/10 |

The diagnostic reused the same Python code and made no model calls. It replaced
only the declared dependency lists so that typing and behavior could be examined:
11 original first attempts had been rejected for declaring unused Pydantic.
The original run also had one grader timeout. The replay is a separate
diagnostic and does not replace the original scores.

The small sample exposed concrete errors with client closure and Bedrock union
types. It does not establish a general agent success rate or a comparison of SDK
performance. Some corrections were left unattempted when the budget ended.

The [full report and raw records](https://github.com/ferrumio/rboto/tree/3a017ac92b2d75477b8235e14018b9c1655f92ea/evals/results)
include each attempt, failures, repetition differences, token usage, environment
fingerprints, and the interrupted format preflight.

## Reproduce the evaluation

The evaluation runner is repository tooling, separate from the installed SDK.
Follow its [environment setup](https://github.com/ferrumio/rboto/blob/3a017ac92b2d75477b8235e14018b9c1655f92ea/evals/README.md)
to install the tools and prepare the grading image from existing native clients.

This command only prints a plan; it makes no inference calls:

```bash
.venv/bin/rboto-agent-evals run \
  --model eu.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --region eu-west-2 --version 0.2.0 --repeats 2 \
  --max-calls 40 --max-output-tokens 2048 --deadline-seconds 900
```

To execute that plan, add `--live`, `--manifest` pointing to the prepared image
manifest, and `--output` pointing to a new results directory. Live mode consumes
model tokens in the configured account. The example limits execution to 40
model requests and 2,048 output tokens per request. The 900-second deadline
stops new calls from starting; an already admitted call and its grading may
finish after it.

Use `regrade` to inspect stored code without generating another response.
Recorded prompts, code, and grading results let you inspect a failure before
spending tokens on another experiment.
