# Application code evaluations

This private development tool measures whether a model can write correct rboto
application code from supplied documentation. It is not a published package or
part of the SDK.

The pilot has five tasks and two conditions:

| Task | Required behavior |
|---|---|
| `s3_projection` | Project native bucket attributes into Pydantic models; handle absent lists and names. |
| `dynamodb_pages` | Query every page, including an empty intermediate page; preserve query inputs and select string attributes. |
| `sqs_ack` | Await processing before deletion; preserve handler errors and skip incomplete messages. |
| `s3_stream` | Hash an entire streamed object; close the stream and client; catch only the modeled missing-key error. |
| `bedrock_input` | Encode nested Converse inputs and handle message/text output variants and absent output. |

`references` supplies the relevant generated API pages. `guides` supplies those
same pages plus existing user guides and their included examples. Both conditions
use the same task, SDK snapshot, dated model, temperature, tool access, type
checker, and grading cases. The only added prompt content is documentation.

There are **no model tools** in this pilot. Documentation is supplied in the
prompt, so recorded documentation reads are zero; file counts, content hashes,
UTF-8 bytes, and actual token usage describe the supplied context. This does not
measure autonomous documentation discovery, shell-agent workflows, or a skill.

## Run the free checks

From the repository root, install the private tools:

```bash
.venv/bin/pip install -e evals
.venv/bin/pytest evals/tests/test_runner.py -q
```

The behavioral fixtures also require installed native clients for S3, DynamoDB,
SQS, and Bedrock Runtime. With those installed:

```bash
RBOTO_TEST_SERVICES=s3,dynamodb,sqs,bedrock-runtime \
  .venv/bin/pytest evals/tests/test_grading.py -q
```

These tests use controlled HTTP peers and fake credentials, with no model calls
or live service resources. Reference solutions must pass both strict mypy and
behavioral assertions. Deliberate mistakes verify that the graders reject
incorrect imports and output access, early pagination termination, premature
acknowledgements, swallowed errors, partial streams, and unclosed resources.
CI runs orchestration tests in the Python job and behavioral cases in each
relevant wheel job.

## Prepare an isolated environment

Use **Linux CPython 3.12.15** and install the four native clients in the Python
environment that runs this tool. Their package versions must match `--version`.
For a published version, installing `rboto[s3,dynamodb,sqs,bedrock-runtime]` at an
exact version is sufficient. For development code, build/install those clients
and the facade from the chosen revision before preparing the evaluation.

Generate the API references using the repository's documentation build. The
runner reads generated `docs/reference/` pages and checked-in `docs/guides/`.
It never reads private graders or reference solutions into model context.

```bash
.venv/bin/python -m rboto_codegen.reference_site
.venv/bin/rboto-agent-evals prepare \
  --version 0.2.0 --directory /tmp/rboto-eval-image
```

Use the global `--sudo` option before `prepare` or `run` if Docker requires
`sudo -n` on your machine.

Preparation copies the **already-installed** Python bindings and native
extensions; it never compiles Rust. It removes debug symbols from copies and
builds an image with a digest-pinned Python base and hashed dependency lock.
Original native files remain intact. The manifest records source and copied
file hashes, the source revision, SDK version, dependencies, and resulting
image ID.

A Git revision does not prove the provenance of an existing compiled library.
The first published experiment uses a development snapshot, not a claim about
the identically numbered PyPI wheel. Rebuilding the recorded source revision
can produce different binary hashes. This protocol is repeatable; neither
binary reproducibility nor deterministic model responses is promised.

## Run a bounded comparison

The model provider uses the host's AWS CLI credentials to call Bedrock Converse.
Choose an accessible **dated** model/profile ID. Do not pass these credentials
to the grading environment.

Without `--live`, this command only prints the free execution plan:

```bash
.venv/bin/rboto-agent-evals run \
  --model eu.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --region eu-west-2 --version 0.2.0 --repeats 2 \
  --max-calls 40 --max-output-tokens 2048 --deadline-seconds 900
```

To execute, add:

```bash
--live --manifest /tmp/rboto-eval-image/manifest.json \
--output /tmp/rboto-eval-results
```

The output directory must not already exist. With these limits, there are at
most 40 model requests and 81,920 generated tokens. Input is limited to 65,536
serialized message bytes per call, including correction history. Token counts
are model-dependent. Dollar cost depends on the selected provider and model;
the runner does not estimate a price from stale rates.

The deadline stops admitting new calls; an admitted model request can run for
up to 75 seconds and its grader for up to 50 seconds. SDK retries are disabled
for the model provider. Every task receives its first attempt before any
correction is attempted. Each failed task can receive one correction using
bounded type-check/behavior feedback. Cases retain paired fixture seeds and
balanced condition order across repetitions.

Candidate code runs in a fresh container with external networking disabled,
fake credentials, a read-only filesystem and project mount, an unprivileged
user, one CPU, 1 GiB memory, a process limit, and a bounded temporary filesystem.
It uses real compiled clients against peers on the container's own loopback
interface. No account service resources are created.

The grader checks declared dependencies against the task's exact requirements;
it does not perform a candidate-controlled pip install. AST restrictions detect
common contract shortcuts. They are not a security sandbox or an adversarial
anti-cheating mechanism. Do not execute model output on the host with
`rboto_evals.grading`.

## Read the results

`results.json` uses schema version 1 and is checkpointed before and after each
attempt. It contains:

- Configuration, source and image fingerprints, dependency versions, timestamps,
  prompt/document hashes, document bytes, and runner source hashes.
- Every planned task, condition, repetition, fixture seed, attempt, and final
  status, including unrun and interrupted tasks.
- First-attempt and eventual success, correction counts, type-error codes,
  behavioral failures, provider tokens, model time, grading time, and variation
  between repetitions.
- Missing token-usage counts for interrupted/provider-failed calls. Summed token
  totals cannot account for usage the provider did not return.

Each task directory also contains the supplied prompt and original model
responses. A response may be plain JSON or exactly one enclosing Markdown JSON
fence. Parsing does not search prose for a plausible solution.

You can diagnose stored submissions without calling a model:

```bash
.venv/bin/rboto-agent-evals regrade \
  --source /tmp/rboto-eval-results \
  --manifest /tmp/rboto-eval-image/manifest.json \
  --output /tmp/rboto-eval-diagnostic.json
```

Adding `--canonical-dependencies` replaces only the declared requirements during
this diagnostic replay. It helps inspect code that the exact dependency check
rejected before type checking. The report retains original requirements and
grades; neither the original score nor the model's correction feedback changes.
Do not present this diagnostic as another independent model run.

The canonical correctness criterion is **strict typing plus behavior**, with
explicit dependency and API-contract checks. Passing mypy alone is not success.
Type-error codes are diagnostic categories, not a perfect count of invented
APIs. `incomplete` means the task exhausted a budget or was interrupted; it is
kept in the planned denominator.

Small samples can expose specific failures. They do not establish that one
model, SDK, or documentation set is generally better. These cases do not test
live AWS compatibility, installation resolution, throughput, or all operations.
See the versioned files under `results/` for the first comparison and its limits.
