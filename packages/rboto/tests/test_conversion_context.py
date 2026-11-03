from dataclasses import FrozenInstanceError

import pytest
from rboto._conversion import attach_conversion_context
from rboto.exceptions import ConversionErrorContext, get_conversion_context


def test_builtin_error_keeps_class_and_receives_bounded_context() -> None:
    error = TypeError("private original detail")
    result = attach_conversion_context(
        error, "service" * 100, "operation" * 100, "field." * 100, "rule" * 100, "type",
    )
    assert result is error
    context = get_conversion_context(error)
    assert isinstance(context, ConversionErrorContext)
    assert context.truncated
    assert len(context.service) == len(context.operation) == 128
    assert len(context.path) == 512
    assert len(context.expected) == 160
    assert "private" not in str(error)
    with pytest.raises(FrozenInstanceError):
        setattr(context, "path", "changed")


def test_application_error_keeps_message_identity_and_cause() -> None:
    cause = LookupError("application cause")
    try:
        raise TypeError("application message") from cause
    except TypeError as error:
        traceback = error.__traceback__
        result = attach_conversion_context(error, "s3", "PutObject", "body", "bytes", "type")
        assert result is error
        assert error.__cause__ is cause
        assert error.__traceback__ is traceback
        assert str(error) == "application message"
        assert error.__notes__ == ["body: expected bytes (s3.PutObject; type)"]
        assert get_conversion_context(error) == ConversionErrorContext(
            "s3", "PutObject", "body", "bytes", "type",
        )


def test_unrelated_errors_have_no_conversion_context() -> None:
    error = RuntimeError("unrelated")
    assert get_conversion_context(error) is None
    setattr(error, "conversion_context", {"path": "not typed context"})
    assert get_conversion_context(error) is None
