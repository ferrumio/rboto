# Native CLI

`rboto` is a standalone Rust executable containing every service and operation in
the rboto service catalog. It runs without Python or installed rboto packages.

Build it from a checkout with Rust 1.94.1 or newer:

```bash
CARGO_BUILD_JOBS=1 cargo build --manifest-path cli/Cargo.toml --release --locked
./cli/target/release/rboto --services
./cli/target/release/rboto s3 --help
./cli/target/release/rboto s3 get-object --help
```

Copy `cli/target/release/rboto` onto your `PATH`. Binaries must be built for the
destination operating system and architecture. This CLI is currently built from
source; the repository CI also produces a Linux x86_64 artifact.

## Commands and credentials

Service and operation names use hyphens. Operation names also accept `snake_case`
and their modeled API spelling. JSON member names use `snake_case`, including
nested members. Credentials use the standard provider chain, including environment
variables, profiles and role credentials.

```bash
rboto sts get-caller-identity --profile development --region us-east-1
rboto eventbridge list-rules --region us-east-1
rboto pipes list-pipes --region us-east-1
rboto scheduler list-schedules --region us-east-1
```

Use `--endpoint-url URL` for a compatible local endpoint. HTTPS verifies certificates.
`--max-attempts N` controls SDK retries; `--timeout SECONDS` limits configuration loading
and the entire operation, including pagination and response streaming.

## JSON input and output

Pass parameters directly, load a JSON object from a file, or use `--input -` for stdin:

```bash
rboto lambda get-function --function-name example --region us-east-1

rboto bedrock-runtime converse --region us-east-1 --input '{
  "model_id": "your-model-id",
  "messages": [{"role": "user", "content": [{"text": "Hello"}]}],
  "inference_config": {"max_tokens": 64}
}'

rboto eventbridge put-events --input events.json --region us-east-1
```

Direct options override the corresponding fields from `--input`. Objects, arrays,
numbers and booleans use JSON syntax. Strings and enums are passed as text. Unions
use one variant, for example `{"text":"Hello"}`. Timestamps use RFC 3339.
Unknown parameters, missing required members and invalid types fail with a nonzero
exit status.

Use `--param 'name=JSON'` when an API parameter conflicts with a CLI option.
`--validate` checks top-level names and required fields without making a request;
it does not validate all nested types or remote service constraints.

Responses are JSON on stdout. `--pretty` indents ordinary responses. Errors are JSON
on stderr, retaining modeled error codes and request IDs when supplied. Exit codes:
`0` success, `1` request/IO failure, `2` invalid input, `130` interruption.

## Files

```bash
rboto s3 get-object --bucket example --key archive.tar \
  --output-file archive.tar --region us-east-1

rboto s3 put-object --bucket example --key archive.tar \
  --bytes body=archive.tar --region us-east-1

rboto lambda invoke --function-name example --bytes payload=request.json \
  --output-file response.json --region us-east-1
```

Byte streams transfer incrementally. Blob fields follow the SDK's owned-buffer
semantics. Binary JSON values are base64 strings; `{"$file":"path"}` also works in
nested blob fields. `--bytes MEMBER=FILE` is shorthand for a top-level file reference.
Binary stdin is not supported.

File output is written beside the destination in a temporary file and replaces the
destination only after a complete transfer. A failed download preserves an existing
destination. Use `--output-file -` to stream binary bytes to stdout; response metadata
then goes to stderr. If an operation has several binary outputs, select one with
`--output-member NAME`.

These are modeled API commands. High-level `s3 cp` and `s3 sync` commands are not included.

## Pagination and event streams

Pagination is explicit. Operations with an SDK paginator support:

```bash
rboto sns list-topics --paginate --max-pages 10 --region us-east-1
```

Each page is a separate JSON record. Pagination does not combine pages in memory.
Without `--paginate`, the CLI makes one operation call. It cannot combine pagination
with binary file output.

Event responses are compact NDJSON: the first line contains response metadata and
subsequent lines contain `{"stream":"member","event":{...}}`. Input events accept
JSON arrays or NDJSON files/stdin:

```bash
rboto bedrock-runtime invoke-model-with-bidirectional-stream \
  --model-id your-model-id --events body=events.ndjson --region us-east-1
```

`--events body=-` reads events from stdin on demand, allowing responses while input is
still arriving. Each line must be one event object; binary members use base64.
A line is limited to 16 MiB. Stdin can supply only one input source.
`--pretty` is rejected for event responses to preserve NDJSON framing.

The CLI passes requests through the pinned service SDK. Permissions, regional
availability and API-specific validation still apply. Catalog coverage and local
protocol tests do not imply that every operation has been exercised against a live service.
