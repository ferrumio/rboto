# ADR-0030: Stream request bodies from Python async iterables

- Status: Accepted
- Date: 2026-11-07

## Context

ADR-0004 keeps response bodies as Rust-owned streams, but every request body modeled as a
Smithy streaming blob was accepted only as `bytes`. The generated adapter copied the whole
payload into a `Vec<u8>` under the GIL before the Tokio future started. For S3 `PutObject`
and `UploadPart`, Lambda `InvokeAsync`, and Lex `RecognizeUtterance` this meant two full
copies of the payload in memory, an event-loop stall proportional to the payload size, and
no way to upload data that does not fit in memory or that is produced incrementally.

The AWS SDK for Rust accepts any `http_body::Body`, but S3 refuses streaming uploads of
unknown size because its checksum and signing pipeline needs the exact length before the
first byte is sent. A streamed body is also consumed once; the SDK cannot replay it for
retries.

## Decision

Streaming blob inputs accept `bytes | AsyncIterator[bytes]`.

- `bytes` and `bytearray` keep the existing in-memory, replayable body.
- Any object exposing `__aiter__` is wrapped by `rboto_core::python_byte_stream` into an
  `http_body::Body` that awaits one `__anext__` per poll. The poll runs through the same
  `rboto._streams._EventPoll` boundary as event-stream inputs (ADR-0029), so it executes on
  the originating event loop, is cancelled when the request is dropped, and can consume
  native rboto iterators such as a response `ByteStream`. There is no producer task and no
  buffer beyond the chunk in flight. Each chunk must be `bytes`.
- When the operation models a `content_length` member next to the streaming body, the
  generated method forwards it as the body's exact size hint and raises `ValueError` before
  sending if an async iterable arrives without it. Operations without `content_length`
  send an unsized body. The hint tracks the remaining bytes as chunks are consumed.
  A missing length keeps the body field path and the specific requirement in the
  ADR-0028 conversion context.
- A Python exception raised by the producer propagates unchanged. `sdk_error_to_py` walks
  the dispatch failure's source chain, finds the `PyErr`, and re-raises it instead of
  mapping it to `ConnectionError`.

Event-stream inputs and streamed bodies share one async-iterator driver in `rboto-core`,
so both input stream kinds have one ownership, cancellation, and error model. After a
conversion failure, the stream ends without requesting another item from the producer.

## Consequences

- Large uploads no longer require the payload in memory, and the event loop is not blocked
  by a payload-sized copy.
- Streamed bodies are not retried by the SDK on transient failures; callers who need
  retries keep using `bytes` or re-run the upload themselves.
- `content_length` must match the bytes actually yielded. rboto does not buffer the
  producer to validate the length in advance.
- Invariant 7 widens: streaming blob inputs are typed mappings, literals, tagged unions, or
  `bytes | AsyncIterator[bytes]`.
- Generated Python signatures changed for the affected operations only.

## Alternatives considered

- File-path inputs via `ByteStream::from_path`: deferred. They are replayable and sized,
  but an async iterable over a file already covers the use case and keeps one contract.
- A dedicated upload helper or multipart manager: out of scope for the generated client;
  it can be built on top of `upload_part` with streamed bodies.
- Buffering the iterable to compute the length automatically: rejected because it
  reintroduces the memory cost the change removes.
- A detached producer task with a channel: rejected because it adds an unbounded buffer and
  a second owner of the Python iterator.

## References

- ADR-0004, ADR-0028, ADR-0029
- `crates/rboto-core/src/byte_stream.rs`
- `crates/rboto-core/src/async_iter.rs`
- `codegen/src/rboto_codegen/generator.py`
- `tests/integration/test_s3_client_methods.py`
