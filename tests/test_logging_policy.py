"""Tests for static runtime logging privacy policy."""
from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from pathlib import Path

from railway_logging import MaxLevelFilter
from scripts.validate_logging_policy import validate_logging_policy


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_repository_logging_policy_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    assert validate_logging_policy(root) == []


def test_railway_logging_config_uses_stream_only_severity_routing() -> None:
    config = json.loads(Path("logging.railway.json").read_text(encoding="utf-8"))
    handlers = config["handlers"]

    assert config["version"] == 1
    assert config["disable_existing_loggers"] is False
    assert handlers["stdout"]["stream"] == "ext://sys.stdout"
    assert handlers["stdout"]["filters"] == ["max_info"]
    assert handlers["access_stdout"]["stream"] == "ext://sys.stdout"
    assert handlers["access_stdout"]["filters"] == ["max_info"]
    assert handlers["stderr"]["stream"] == "ext://sys.stderr"
    assert handlers["stderr"]["level"] == "WARNING"
    assert all(
        handler["class"] == "logging.StreamHandler"
        for handler in handlers.values()
    )
    assert config["filters"]["max_info"] == {
        "()": "railway_logging.MaxLevelFilter",
        "max_level": "INFO",
    }
    assert config["root"]["handlers"] == ["stdout", "stderr"]
    assert config["loggers"]["uvicorn"]["handlers"] == ["stdout", "stderr"]
    assert config["loggers"]["uvicorn.access"]["handlers"] == [
        "access_stdout",
        "stderr",
    ]
    assert config["formatters"]["access"]["fmt"] == (
        "%(levelprefix)s HTTP %(status_code)s"
    )

    encoded_formatters = json.dumps(config["formatters"], sort_keys=True).casefold()
    for forbidden in (
        "authorization",
        "headers",
        "payload",
        "body",
        "phone",
        "recipient",
        "token",
        "secret",
        "password",
        "cookie",
        "database_url",
        "document",
        "ciphertext",
        "client_addr",
        "request_line",
    ):
        assert forbidden not in encoded_formatters


def test_railway_logging_config_loads_and_routes_real_records() -> None:
    script = """
import json
import logging
from logging.config import dictConfig
from pathlib import Path

config = json.loads(Path('logging.railway.json').read_text(encoding='utf-8'))
dictConfig(config)
logging.getLogger('amthero24.synthetic').info('synthetic-info')
logging.getLogger('amthero24.synthetic').error('synthetic-error')
logging.getLogger('uvicorn.access').info(
    '%s - \"%s %s HTTP/%s\" %d',
    '198.51.100.25',
    'GET',
    '/private?token=synthetic-secret',
    '1.1',
    200,
)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "synthetic-info" in completed.stdout
    assert "synthetic-error" not in completed.stdout
    assert "HTTP 200" in completed.stdout
    assert "198.51.100.25" not in completed.stdout
    assert "/private" not in completed.stdout
    assert "synthetic-secret" not in completed.stdout
    assert "synthetic-info" not in completed.stderr
    assert "synthetic-error" in completed.stderr


def test_max_level_filter_sends_info_to_stdout_and_errors_to_stderr() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    stdout_handler = logging.StreamHandler(stdout)
    stdout_handler.addFilter(MaxLevelFilter("INFO"))
    stderr_handler = logging.StreamHandler(stderr)
    stderr_handler.setLevel(logging.WARNING)
    logger = logging.Logger("amthero24.synthetic", level=logging.DEBUG)
    logger.propagate = False
    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)

    logger.debug("synthetic-debug")
    logger.info("synthetic-info")
    logger.warning("synthetic-warning")
    logger.error("synthetic-error")

    stdout_value = stdout.getvalue()
    stderr_value = stderr.getvalue()
    assert "synthetic-debug" in stdout_value
    assert "synthetic-info" in stdout_value
    assert "synthetic-warning" not in stdout_value
    assert "synthetic-error" not in stdout_value
    assert "synthetic-debug" not in stderr_value
    assert "synthetic-info" not in stderr_value
    assert "synthetic-warning" in stderr_value
    assert "synthetic-error" in stderr_value


def test_detects_direct_sensitive_logging_arguments(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(
        source,
        "import logging\nlogger = logging.getLogger(__name__)\n"
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
        "import logging\nlogger = logging.getLogger(__name__)\n"
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
        "import logging\nlogger = logging.getLogger(__name__)\n"
        'logger.info("request failed", extra={"authorization": authorization})\n',
    )
    findings = validate_logging_policy(tmp_path, files=[source])
    assert any(item.identifier == "authorization" for item in findings)


def test_message_id_extra_is_allowed_only_because_runtime_always_redacts_it(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(
        source,
        "import logging\nlogger = logging.getLogger(__name__)\n"
        'logger.exception("message failed", extra={"message_id": message.message_id})\n',
    )
    assert validate_logging_policy(tmp_path, files=[source]) == []


def test_mixed_extra_mapping_still_rejects_sensitive_data(tmp_path) -> None:
    source = tmp_path / "service.py"
    _write(
        source,
        "import logging\nlogger = logging.getLogger(__name__)\n"
        'logger.info("request failed", extra={"message_id": message.message_id, "payload": payload})\n',
    )
    findings = validate_logging_policy(tmp_path, files=[source])
    assert any(item.identifier in {"message", "payload"} for item in findings)
