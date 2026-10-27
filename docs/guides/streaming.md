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

`put_object(body=...)` accepts bytes. Multipart operations are individual service
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
