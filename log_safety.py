"""Privacy-safe logging primitives for AmtHero24.

The module installs a global ``LogRecord`` factory so handlers created later by Uvicorn
or libraries receive already-sanitized records. It never hashes or persists raw values;
redaction happens in memory immediately before formatting.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

_REDACTED = "[REDACTED]"
_MAX_LOG_TEXT = 2_000
_SENSITIVE_ENV_NAMES = {
    "DATABASE_URL",
    "GROQ_API_KEY",
    "WHATSAPP_TOKEN",
    "META_APP_SECRET",
    "VERIFY_TOKEN",
    "ADMIN_API_TOKEN",
    "REMINDER_ENCRYPTION_KEY",
    "REMINDER_OLD_ENCRYPTION_KEY",
    "REMINDER_LEGACY_WHATSAPP_TOKEN",
    "SUPPORT_API_TOKEN",
    "SUPPORT_ENCRYPTION_KEY",
    "BACKUP_ENCRYPTION_KEY",
}
_SENSITIVE_KEY_PARTS = {
    "authorization",
    "access_token",
    "api_key",
    "app_secret",
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "phone",
    "phone_number",
    "wa_id",
    "recipient",
    "ciphertext",
    "message",
    "messages",
    "message_id",
    "body",
    "text",
    "caption",
    "document",
    "content",
    "payload",
    "raw_body",
}
_STANDARD_RECORD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "request_id",
}

_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_BASIC_PATTERN = re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}")
_DATABASE_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b((?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://[^\s:/@]+:)([^\s@/]+)(@)"
)
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:access_token|api_key|key|password|secret|signature|token)=)([^&#\s]+)"
)
_KNOWN_TOKEN_PATTERN = re.compile(
    r"\b(?:gsk_[A-Za-z0-9_-]{16,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
    r"sk_live_[A-Za-z0-9]{12,}|xox[baprs]-[A-Za-z0-9-]{16,}|"
    r"AIza[0-9A-Za-z_-]{24,}|EAA[A-Za-z0-9]{32,})\b"
)
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+|00)(?:[\s()./-]*\d){8,15}(?!\w)")
_CONTEXT_PHONE_PATTERN = re.compile(
    r"(?i)(\b(?:from|to|phone|phone_number|recipient|wa_id)\b\s*[:=]\s*[\"']?)([+0-9][0-9\s()./-]{7,20})"
)
_FIELD_PATTERN = re.compile(
    r"(?is)([\"']?(?:authorization|access_token|api_key|app_secret|password|secret|token|"
    r"cookie|phone|phone_number|wa_id|recipient|ciphertext|message|body|text|caption|document|"
    r"payload|raw_body)[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)


class RedactedLogException(RuntimeError):
    """Exception shell used only to prevent sensitive exception messages reaching logs."""


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").strip().casefold().replace("-", "_")
    if not normalized:
        return False
    if normalized in _SENSITIVE_KEY_PARTS:
        return True
    return any(
        normalized.endswith(suffix)
        for suffix in (
            "_token",
            "_secret",
            "_password",
            "_api_key",
            "_phone",
            "_message_id",
            "_ciphertext",
        )
    )


def _environment_secrets(environment: Mapping[str, str] | None = None) -> tuple[str, ...]:
    source = environment if environment is not None else os.environ
    values: set[str] = set()
    for name, raw in source.items():
        upper = str(name).upper()
        if upper not in _SENSITIVE_ENV_NAMES and not upper.endswith(("_TOKEN", "_SECRET", "_PASSWORD", "_KEY")):
            continue
        value = str(raw or "").strip()
        if len(value) >= 8:
            values.add(value)
    return tuple(sorted(values, key=len, reverse=True))


def redact_text(value: Any, *, environment: Mapping[str, str] | None = None) -> str:
    """Redact credential/phone patterns and bound log-message length."""
    text = str(value or "")
    for secret in _environment_secrets(environment):
        text = text.replace(secret, _REDACTED)
    text = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", text)
    text = _BASIC_PATTERN.sub(f"Basic {_REDACTED}", text)
    text = _DATABASE_CREDENTIAL_PATTERN.sub(rf"\1{_REDACTED}\3", text)
    text = _QUERY_SECRET_PATTERN.sub(rf"\1{_REDACTED}", text)
    text = _KNOWN_TOKEN_PATTERN.sub(_REDACTED, text)
    text = _PHONE_PATTERN.sub("[REDACTED_PHONE]", text)
    text = _CONTEXT_PHONE_PATTERN.sub(r"\1[REDACTED_PHONE]", text)
    text = _FIELD_PATTERN.sub(rf"\1\2{_REDACTED}\4", text)
    if len(text) > _MAX_LOG_TEXT:
        text = text[:_MAX_LOG_TEXT] + "...[TRUNCATED]"
    return text


def redact_value(value: Any, *, key: Any = None, environment: Mapping[str, str] | None = None) -> Any:
    """Recursively sanitize a logging argument while retaining useful safe structure."""
    if _is_sensitive_key(key):
        return _REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value, environment=environment)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[REDACTED_BYTES length={len(value)}]"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item_value, key=item_key, environment=environment)
            for item_key, item_value in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_value(item, environment=environment) for item in value)
    if isinstance(value, list):
        return [redact_value(item, environment=environment) for item in value]
    if isinstance(value, set):
        return {redact_value(item, environment=environment) for item in value}
    if isinstance(value, Sequence):
        return [redact_value(item, environment=environment) for item in value]
    return redact_text(value, environment=environment)


def _safe_exc_info(exc_info: Any, *, environment: Mapping[str, str] | None = None) -> Any:
    if not isinstance(exc_info, tuple) or len(exc_info) != 3 or exc_info[1] is None:
        return exc_info
    exception = exc_info[1]
    safe_message = redact_text(str(exception), environment=environment)
    safe = RedactedLogException(f"{type(exception).__name__}: {safe_message}")
    return (type(safe), safe, None)


def _active_request_id() -> str:
    """Resolve the current random correlation ID without creating an import cycle."""
    try:
        from http_boundary import current_request_id

        return current_request_id()
    except Exception:
        return ""


def sanitize_log_record(record: logging.LogRecord, *, environment: Mapping[str, str] | None = None) -> logging.LogRecord:
    """Mutate one record before any formatter or handler can expose its values."""
    record.msg = redact_value(record.msg, environment=environment)
    if isinstance(record.args, Mapping):
        record.args = redact_value(record.args, environment=environment)
    elif isinstance(record.args, tuple):
        record.args = tuple(redact_value(item, environment=environment) for item in record.args)
    elif record.args:
        record.args = redact_value(record.args, environment=environment)
    record.exc_info = _safe_exc_info(record.exc_info, environment=environment)
    record.exc_text = None
    if record.stack_info:
        record.stack_info = redact_text(record.stack_info, environment=environment)

    for key in tuple(record.__dict__):
        if key in _STANDARD_RECORD_FIELDS:
            continue
        record.__dict__[key] = redact_value(record.__dict__[key], key=key, environment=environment)

    request_id = _active_request_id()
    if request_id:
        record.request_id = request_id
        marker = f"[request_id={request_id}]"
        if marker not in str(record.msg):
            record.msg = f"{record.msg} {marker}"
    return record


def install_logging_safety() -> None:
    """Install one idempotent global factory that also protects future handlers."""
    current = logging.getLogRecordFactory()
    if getattr(current, "_amthero24_log_safety", False):
        return

    def safe_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        return sanitize_log_record(record)

    setattr(safe_factory, "_amthero24_log_safety", True)
    logging.setLogRecordFactory(safe_factory)
