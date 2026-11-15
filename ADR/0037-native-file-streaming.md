# ADR-0037: Native file sources and response sinks

- Status: Accepted
- Date: 2026-11-15
- Extends: ADR-0004, ADR-0018, ADR-0029, ADR-0030, ADR-0032, ADR-0036

## Decision

Expose a pure-Python immutable `FileBody` descriptor from `rboto`. Constructing it
validates path/buffer arguments without filesystem I/O. Only modeled streaming
blob inputs accept it. Generic codegen extracts ordinary inputs synchronously,
then resolves file metadata/body construction inside the managed native operation.
Keep ordinary blobs, Python async producers, event inputs and existing byte bodies
unchanged. Generated Python signatures remain keyword-only and preserve their
one-to-one native operation alignment.

Each call snapshots file metadata and uses a replayable native body factory.
Each attempt opens an independent reader, verifies regular-file status, size,
modification time and Unix file identity, and checks the open file again at EOF.
Readers use bounded buffers and report premature EOF. Native file failures retain
`OSError` through the request-body error chain. Input size/type failures carry the
operation and body field context. Explicit content length must equal the full
file's size. There is no offset/range or arbitrary replayable producer API.

The application must keep file content stable until completion. Metadata checks
detect common replacements/mutations but are not a snapshot or file lock. They
cannot establish consistency against a writer that restores metadata or races
the final check.

Expose `ByteStream.write_to(path) -> Awaitable[int]` through the shared core and
generated native stubs. Exclusively create a new destination before taking the
remaining body. A destination-open failure leaves the stream available. On
success, flush buffered writes and return the byte count. Exhausted streams
produce empty files. Explicitly closed streams reject writing before file creation.

After opening the destination, cancellation/error consumes the remaining body and
may leave a partial file. Preserve managed-resource closure, independent response
ownership and stream error context. No detached copy task, filesystem sync,
overwrite, atomic replacement, transfer concurrency or multipart orchestration
is introduced. Blocking filesystem work already submitted by Tokio may finish
after cancellation; cancellation does not undo completed writes.

## Validation

Compare optimized native paths with the existing Python producer/write paths and
a Python write-batching control. Use separate local HTTP peers, fresh randomized
worker processes, bounded files, integrity hashes, asyncio and Trio. Record wall
time, process CPU, RSS and event-loop gaps; do not extrapolate local timings to AWS.

Compiled tests cover empty/nonaligned bodies, retries, source mutation/removal,
independent concurrent readers, checksum trailers, invalid lengths, destination
errors, truncated responses, partial consumption, cancellation and close races.
Regenerate the full catalog and retain strict type checks.
