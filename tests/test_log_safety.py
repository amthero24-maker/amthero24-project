"""Tests for runtime privacy-safe logging."""
from __future__ import annotations

import logging
from io import StringIO

from log_safety import install_logging_safety, redact_text, redact_value, sanitize_log_record


def test_redact_text_removes_environment_secret_bearer_database_and_phone() -> None:
    secret = "operator-secret-value-123456789"
    database_url = "postgresql://" + "service:database-password@db.example/app"
    text = (
        f"Authorization: Bearer bearer-token-value-123456 "
        f"database={database_url} "
        f"phone=+49 151 23456789 custom={secret}"
    )
    redacted = redact_text(text, environment={"ADMIN_API_TOKEN": secret})
    assert secret not in redacted
    assert "bearer-token-value" not in redacted
    assert "database-password" not in redacted
    assert "+49 151 23456789" not in redacted
    assert redacted.count("[REDACTED]") >= 3
    assert "[REDACTED_PHONE]" in redacted


def test_durable_queue_encryption_key_is_redacted_as_an_exact_environment_secret() -> None:
    key = "durable-queue-private-key-2026-unique-A7mQ2xP9"
    redacted = redact_text(
        f"queue initialization failed for key={key}",
        environment={"MESSAGE_QUEUE_ENCRYPTION_KEY": key},
    )
    assert key not in redacted
    assert "[REDACTED]" in redacted


def test_redact_text_removes_sensitive_json_fields_and_query_values() -> None:
    text = (
        'payload={"message":"private user words","token":"abc123456789",'
        '"phone_number":"4915123456789"} '
        "https://example.invalid/callback?token=query-secret-value&safe=yes"
    )
    redacted = redact_text(text)
    assert "private user words" not in redacted
    assert "abc123456789" not in redacted
    assert "4915123456789" not in redacted
    assert "query-secret-value" not in redacted
    assert "safe=yes" in redacted


def test_redact_value_preserves_safe_structure_and_removes_sensitive_keys() -> None:
    value = {
        "provider": "groq",
        "status": 503,
        "authorization": "Bearer hidden-token-value",
        "payload": {"text": "private message", "attempt": 2},
        "items": ["safe", {"recipient": "+4915123456789"}],
        "message_id": "wamid.platform-identifier-123456789",
        "binary": b"private document bytes",
    }
    redacted = redact_value(value)
    assert redacted["provider"] == "groq"
    assert redacted["status"] == 503
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["payload"] == "[REDACTED]"
    assert redacted["items"][1]["recipient"] == "[REDACTED]"
    assert redacted["message_id"] == "[REDACTED]"
    assert redacted["binary"].startswith("[REDACTED_BYTES")


def test_sanitize_log_record_redacts_args_extras_and_exception_message() -> None:
    secret = "runtime-secret-value-abcdefgh"
    message_id = "wamid.platform-identifier-123456789"
    try:
        raise RuntimeError(f"request failed with Bearer {secret} for +4915123456789")
    except RuntimeError:
        exc_info = __import__("sys").exc_info()
    record = logging.LogRecord(
        name="amthero24.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failed payload=%s",
        args=({"body": "private body", "status": 500},),
        exc_info=exc_info,
    )
    record.phone = "+4915123456789"
    record.message_id = message_id
    sanitize_log_record(record, environment={"ADMIN_API_TOKEN": secret})
    rendered = logging.Formatter("%(message)s %(phone)s %(message_id)s").format(record)
    assert secret not in rendered
    assert "private body" not in rendered
    assert "+4915123456789" not in rendered
    assert message_id not in rendered
    assert "RuntimeError" in rendered
    assert "[REDACTED]" in rendered


def test_global_factory_protects_handlers_installed_after_startup() -> None:
    install_logging_safety()
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("amthero24.log-safety-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("sending to %s with %s", "+4915123456789", "Bearer abcdefghijklmnop")
    output = stream.getvalue()
    assert "+4915123456789" not in output
    assert "abcdefghijklmnop" not in output
    assert "[REDACTED_PHONE]" in output
    assert "[REDACTED]" in output


def test_log_text_is_bounded_after_redaction() -> None:
    redacted = redact_text("x" * 10_000)
    assert len(redacted) < 2_100
    assert redacted.endswith("...[TRUNCATED]")
