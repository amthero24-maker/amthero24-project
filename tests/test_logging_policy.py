"""Tests for static runtime logging privacy policy."""
from __future__ import annotations

from pathlib import Path

from scripts.validate_logging_policy import validate_logging_policy


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_repository_logging_policy_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    assert validate_logging_policy(root) == []


def test_detects_direct_sensitive_logging_arguments(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(
        source,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        'logger.info("incoming %s", phone_number)\n'
        'logger.error(f"payload={payload}")\n',
    )

    findings = validate_logging_policy(tmp_path, files=[source])

    identifiers = {item.identifier for item in findings}
    assert "phone_number" in identifiers
    assert "payload" in identifiers
    assert all(item.rule == "sensitive-log-argument" for item in findings)


def test_detects_runtime_print(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(source, 'print("debug")\n')

    findings = validate_logging_policy(tmp_path, files=[source])

    assert len(findings) == 1
    assert findings[0].rule == "runtime-print"


def test_accepts_constant_events_and_explicit_redaction(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(
        source,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        'logger.info("worker started")\n'
        'logger.warning("recipient=%s", redact_text(phone_number))\n'
        'logger.error("payload=%s", redact_value(payload))\n',
    )

    assert validate_logging_policy(tmp_path, files=[source]) == []


def test_scripts_tests_and_operator_smoke_cli_are_excluded(tmp_path) -> None:
    _write(tmp_path / "scripts" / "cli.py", 'print("operator output")\n')
    _write(tmp_path / "tests" / "test_debug.py", 'print("test output")\n')
    _write(tmp_path / "production_smoke.py", 'print("read-only smoke output")\n')
    _write(tmp_path / "service.py", "value = 1\n")

    assert validate_logging_policy(tmp_path) == []


def test_sensitive_extra_mapping_is_detected(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(
        source,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        'logger.info("request failed", extra={"authorization": authorization})\n',
    )

    findings = validate_logging_policy(tmp_path, files=[source])

    assert any(item.identifier == "authorization" for item in findings)


def test_message_id_extra_is_allowed_only_because_runtime_always_redacts_it(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(
        source,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        'logger.exception("message failed", extra={"message_id": message.message_id})\n',
    )

    assert validate_logging_policy(tmp_path, files=[source]) == []


def test_mixed_extra_mapping_still_rejects_sensitive_data(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(
        source,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        'logger.info("request failed", extra={"message_id": message.message_id, "payload": payload})\n',
    )

    findings = validate_logging_policy(tmp_path, files=[source])

    assert any(item.identifier in {"message", "payload"} for item in findings)
