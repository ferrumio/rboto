# Streaming

rboto exposes byte streams and modeled event streams as async iterators.

## Byte streams

Read an S3 body in chunks:

```python
--8<-- "examples/docs/download.py"
```

The file writes run in a worker thread. Each write finishes before the next chunk
is requested. A failed download may leave a partial destination file.

For a body that fits in memory:

```python
--8<-- "examples/docs/read_object.py"
```

`.read()` collects the remaining bytes. Streams cannot be rewound. Each response
owns its stream; give each stream one consumer.
Use `async with response.body` or `await response.body.aclose()` to release a
partially consumed body. Event receivers support the same lifecycle methods.
See [Closing clients and streams](lifecycle.md) for cancellation and ownership.

## Files

Use `FileBody` for a file upload and `write_to()` to save a response body:

```python
--8<-- "examples/docs/native_file_streams.py"
```

File reads and writes run on the native runtime. These paths do not create a
Python `bytes` object for each chunk. They work with asyncio and Trio.
`FileBody` is accepted by modeled streaming blob parameters across services;
ordinary `bytes` parameters, such as a JSON document body, are unchanged.

`FileBody(path, buffer_size=1024 * 1024)` describes a regular file without opening
it. Each operation resolves the path and size asynchronously. Omit `content_length`
to use that size; an explicit length must match the complete file. Paths can be
strings or `Path` objects. The buffer size is an integer from 1 byte to 16 MiB.

Each retry opens a fresh reader. Keep the source unchanged until the operation
finishes, including retries. rboto checks size, modification time and, on Unix,
file identity when opening each attempt, and checks the open file again at EOF.
Detected changes or read failures raise `OSError`. These checks are not a filesystem
snapshot and cannot detect every in-place modification. Concurrent calls may
reuse one `FileBody`; their readers are independent.

`await response.body.write_to(path)` creates a **new file** and returns the number
of remaining bytes written. An existing destination raises `FileExistsError`
without overwriting it or consuming the body. Missing parent directories are
not created. File writes use a bounded buffer and flush before returning;
completion does not include `fsync` or guarantee crash durability.

Once the destination opens, writing consumes the remaining stream, like `read()`.
A stream error, disk error, cancellation or explicit stream closure can leave a
partial file. The remaining body is discarded. Use a temporary destination and
rename it after success when your application needs an atomic replacement.
Closing the client does not close an already-returned response stream.

## Request bodies

Operations that upload a body, such as S3 `put_object` and `upload_part`, accept `bytes`,
`FileBody`, or an async iterable of `bytes`. An async iterable is pulled one chunk at a time as the
SDK sends the request; rboto does not collect the whole iterable before sending. S3 requires
`content_length` for an async iterable; `FileBody` discovers its size. A response `ByteStream` is itself an async
iterator, so one object can be copied into another without buffering:

```python
--8<-- "examples/docs/s3_copy_stream.py"
```

The producer runs on the calling event loop, and each chunk is copied into Rust.
Keep chunks bounded and move blocking work, such as file reads, to a worker thread.
Cancelling the request cancels its pending producer poll.

Async-iterable bodies are consumed once and are not retried by the SDK; `bytes`
and stable `FileBody` sources remain replayable. See [S3](s3.md) for a file upload. Multipart operations are individual service
methods; the Python API does not include a high-level transfer manager.

## Event streams

Event-stream responses are typed mappings containing receivers:

```python
--8<-- "examples/docs/live_tail.py"
```

Inspect the tagged event variant your application needs. Iteration can raise an
error after the initial call has succeeded, so include iteration in error handling.

## Bidirectional streams

Pass a typed `AsyncIterator` of input events. The SDK pulls input events as it sends
them; the returned iterator supplies output events.

```python
--8<-- "examples/docs/bidirectional.py"
```

This helper forwards already encoded events. Input bytes and output interpretation
depend on the selected model's protocol.
