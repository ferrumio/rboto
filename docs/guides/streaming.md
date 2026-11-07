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

## Request bodies

Operations that upload a body, such as S3 `put_object` and `upload_part`, accept `bytes`
or any async iterable of `bytes`. An async iterable is pulled one chunk at a time as the
SDK sends the request, with no buffer beyond the chunk in flight. S3 requires
`content_length` for a streamed body. A response `ByteStream` is itself an async
iterator, so one object can be copied into another without buffering:

```python
--8<-- "examples/docs/s3_copy_stream.py"
```

Streamed bodies are consumed once and are not retried by the SDK; `bytes` bodies remain
replayable. See [S3](s3.md) for a file upload. Multipart operations are individual service
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
