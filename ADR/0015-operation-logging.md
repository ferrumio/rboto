# ADR-0015: Implement opt-in logging as operation middleware

- Status: Accepted
- Date: 2026-10-28
- Deciders: rboto maintainers

## Context

Applications need operation timing, retries, and failures across clients while
retaining control of Python logging configuration. Instrumentation must preserve
the operation's result, modeled exception, cancellation, and stream ownership.

## Decision

`LoggingInterceptor` uses the chain from ADR-0014. It emits a completion record at
INFO for success, ERROR for failure, or WARNING for cancellation. DEBUG adds a
bounded summary when native dispatch retried. Records use `rboto.<service>` and a
versioned metadata dictionary in `LogRecord.rboto`.

Registration is explicit. The library does not configure loggers, handlers,
formatters, propagation, or global logging. Disabled levels skip field preparation;
fully disabled logging also skips clocks and trace lookup. The configured
interceptor still participates in the operation middleware chain.

Only explicit operational metadata is included. Inputs, outputs, stream contents,
exception messages, and tracebacks are never inspected. Final outcome and native
failure fields remain separate so cache hits, policy blocks, and recovered errors
are represented accurately. Logging should precede other application middleware
when these outcomes matter.

Correlation uses the invocation context. An optional synchronous provider supplies
trace and span IDs on the operation task at emission time, without a telemetry
dependency. No global mutable context or background task is added.

Ordinary exceptions in logging infrastructure are suppressed to preserve the
operation result or original exception. A failed trace provider omits trace fields.
Delivery failures are not recursively logged or retried. Process-control exceptions
remain subject to normal Python behavior.

## Consequences

- Applications can use existing text, JSON, queue, or platform handlers.
- Slow synchronous handlers can delay the caller; applications own asynchronous
  delivery through their logging configuration.
- These records are diagnostics rather than guaranteed audit delivery.
- Native completion does not include subsequent consumption of response streams.
- This implementation requires no changes to native service adapters.
