"""Shared isolation for compiled integration tests."""

import io
import logging
from collections.abc import Iterator

import pytest
from rboto import get_logger


@pytest.fixture
def json_logging() -> Iterator[io.StringIO]:
    import rboto.logging as module
    from rboto.services import SERVICE_IDS

    loggers = [logging.getLogger("rboto"), *(get_logger(name) for name in SERVICE_IDS)]
    states = [
        (item, item.level, item.disabled, item.propagate, item.handlers[:]) for item in loggers
    ]
    old = module._configured, module._handler, module._operation_logging
    output = io.StringIO()
    try:
        yield output
    finally:
        if module._handler is not None and module._handler is not old[1]:
            module._handler.close()
        module._configured, module._handler, module._operation_logging = old
        for logger, level, disabled, propagate, handlers in states:
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.disabled = disabled
            logger.propagate = propagate
