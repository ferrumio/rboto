# Pilot recorded on 2026-11-08

This is a five-task documentation experiment with
`eu.anthropic.claude-haiku-4-5-20251001-v1:0` in `eu-west-2`, temperature 0, no
tools, and two repetitions per condition. Python is 3.12.15, mypy 2.4.0, and
Pydantic 2.14.3. The SDK is a development snapshot from
`e4ec3b58e4d1b29f9701c6dbd71a5356d3ca8196`, with package version 0.2.0 and
Linux aarch64 native extensions. It is **not a measurement of the 0.2.0 PyPI
wheels**. File hashes and the container image ID are in the reports.

## Original scores

A pass requires the exact declared dependencies, strict typing, the task's API
constraints, and behavior through native clients against controlled HTTP peers.

| Measurement | API references | References + guides |
|---|---:|---:|
| First-attempt passes | 0/10 | 3/10 |
| Passes after available corrections | 1/10 | 4/10 |
| Tasks without their allowed correction when the budget ended | 6 | 5 |
| Correction attempts used | 4 | 2 |
| Model requests | 14 | 12 |
| Returned input tokens | 47,868 | 87,895 |
| Returned output tokens | 6,723 | 5,757 |
| Model request wall time, including CLI overhead | 35.27 s | 33.67 s |

All 20 tasks received a first attempt. The complete comparison took 232.64
seconds, including grading and container overhead. The guide condition passed
2/5 first attempts in repetition one and 1/5 in repetition two.

Eleven first attempts declared Pydantic without using it and were rejected by
the exact dependency check before typing or behavior ran. One first attempt had
a grader timeout. These are different failure categories; neither should be
reported as a service runtime failure.

The correction budget was exhausted, so eventual scores have unequal numbers
of available corrections and are unsuitable for an improvement claim.

## Diagnostic replay of the same code

To inspect what the dependency gate hid, all 26 stored submissions were graded
again with their declared requirements replaced by the task's canonical list.
**No Python code was edited and no model was called.** Original scores and
correction feedback remain unchanged.

First-attempt code passing strict typing and behavior in this replay:

| Task | API references | References + guides |
|---|---:|---:|
| S3 / Pydantic projection | 0/2 | 2/2 |
| DynamoDB pagination | 0/2 | 2/2 |
| SQS acknowledgement | 0/2 | 2/2 |
| S3 streaming and modeled errors | 0/2 | 1/2 |
| Bedrock nested inputs and output variants | 0/2 | 0/2 |
| Total | 0/10 | 7/10 |

The guide condition passed 4/5 and 3/5 across the two repetitions. The replay
had no infrastructure failures; the originally timed-out submission failed
typing when replayed.

Observed code errors included:

- Inventing `client.close()` instead of using `aclose()` or an async context manager.
- Passing a DynamoDB output cursor directly to an input parameter with a
  different union type, and referring to an unimported union variant.
- Treating Bedrock output unions as attribute objects, or calling `.get()` on a
  value that mypy knows only as `object`.
- Closing an S3 client after a successful download but leaving it open when
  `get_object` raises a modeled or other service error.
- Constructing Pydantic models manually instead of validating native attributes
  as requested.

These examples suggest testing a compact lifecycle example in operation
references, clearer union-narrowing examples, and continued use of the typed
DynamoDB paginator. They are follow-up hypotheses, not changes evaluated here.
The dependency instructions/grading also need a separately measured refinement:
an unnecessary allowed dependency currently prevents the main grader from
examining otherwise valid code. The diagnostic replay makes that limitation
visible.

## Interrupted preflight and total budget

An earlier preflight rejected enclosing Markdown JSON fences. It was stopped
after 13 recorded responses and one additional request whose response was not
collected. Those results are retained but excluded from the comparison.
The parser now accepts a single enclosing JSON fence, with regression coverage.

The comparison used the remaining 26 requests: **40 requests total**, including
the interrupted request. Returned usage across both stages was 215,039 input
tokens and 18,673 output tokens. Usage for the interrupted request is unknown
and is not included in those token totals. The output-token ceiling was 2,048
per request; there are no further model calls in the diagnostic replay.

## Evidence and replay

- `2026-11-08-comparison.json`: original configuration, environment, hashes,
  attempts, metrics, failures, and uncompleted corrections.
- `2026-11-08-diagnostic.json`: separate grading with canonical dependencies,
  including each original grade and dependency list.
- `2026-11-08-format-preflight.json`: excluded preflight and interrupted-request accounting.
- `2026-11-08-responses.jsonl`: all 39 collected raw model responses, identified
  by stage, task, condition, repetition, and attempt.

The final runner adds diagnostic replay support after the live experiment.
Recorded runner-file hashes describe the files at experiment start; the task
prompts and behavioral graders are unchanged. All recorded prompt hashes were
checked against the final task definitions and documentation.

To recreate a directory accepted by `regrade`, from the repository root:

```python
import json
import shutil
from pathlib import Path

records = Path("evals/results")
target = Path("/tmp/rboto-recorded-pilot")
target.mkdir()  # Refuse to replace an earlier replay.
shutil.copyfile(records / "2026-11-08-comparison.json", target / "results.json")
for line in (records / "2026-11-08-responses.jsonl").read_text().splitlines():
    entry = json.loads(line)
    if entry["stage"] != "comparison":
        continue
    folder = target / f"{entry['task']}-{entry['condition']}-{entry['repetition']}"
    folder.mkdir(exist_ok=True)
    (folder / f"attempt-{entry['attempt']}.txt").write_text(entry["response"])
```

Then use the `regrade` command described in `evals/README.md`, with an image
prepared from the matching SDK snapshot. The protocol can be repeated; model
responses and rebuilt native binaries are not guaranteed to be identical.

This is a small, task-specific sample from one model with supplied context.
It does not measure agent tool use, documentation retrieval, all services,
installation resolution, live service compatibility, or runtime performance.
It supports investigating the concrete failures above, not a general claim
that documentation or rboto makes agents a particular percentage better.
