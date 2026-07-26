"""Tests for the deterministic environment-variable contract guard."""
from __future__ import annotations

from pathlib import Path

from scripts.validate_environment_contract import (
    collect_environment_uses,
    parse_env_example,
    validate_environment_contract,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_repository_environment_contract_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    findings, _, _ = validate_environment_contract(root)
    assert findings == []


def test_detects_runtime_variable_missing_from_example(tmp_path) -> None:
    _write(tmp_path / ".env.example", "PORT=8000\n")
    _write(tmp_path / "app.py", 'import os\nvalue = os.getenv("NEW_RUNTIME_SETTING")\n')

    findings, uses, _ = validate_environment_contract(tmp_path)

    assert {item.variable for item in uses} == {"NEW_RUNTIME_SETTING"}
    assert any(item.rule == "undocumented-runtime-variable" and item.variable == "NEW_RUNTIME_SETTING" for item in findings)


def test_detects_stale_documented_variable(tmp_path) -> None:
    _write(tmp_path / ".env.example", "PORT=8000\nOLD_SETTING=true\n")
    _write(tmp_path / "app.py", "value = 1\n")

    findings, _, _ = validate_environment_contract(tmp_path)

    assert any(item.rule == "unused-documented-variable" and item.variable == "OLD_SETTING" for item in findings)
    assert not any(item.variable == "PORT" for item in findings)


def test_detects_nonempty_sensitive_example_without_returning_value(tmp_path) -> None:
    secret = "real-value-that-must-not-be-committed"
    example = tmp_path / ".env.example"
    _write(example, f"ADMIN_API_TOKEN={secret}\n")

    documented, findings = parse_env_example(example)

    assert documented["ADMIN_API_TOKEN"][0] == secret
    assert any(item.rule == "example-secret-value" for item in findings)
    assert all(secret not in item.message for item in findings)


def test_collects_os_environ_and_wrapper_calls(tmp_path) -> None:
    source = tmp_path / "settings.py"
    _write(
        source,
        "import os\n"
        'a = os.environ["DIRECT_SETTING"]\n'
        'b = os.environ.get("OPTIONAL_SETTING")\n'
        'c = required_env("REQUIRED_SETTING")\n'
        'd = _env_bool("BOOLEAN_SETTING")\n',
    )

    uses, findings = collect_environment_uses(tmp_path, files=[source])

    assert findings == []
    assert {item.variable for item in uses} == {
        "DIRECT_SETTING",
        "OPTIONAL_SETTING",
        "REQUIRED_SETTING",
        "BOOLEAN_SETTING",
    }


def test_test_files_do_not_define_runtime_contract(tmp_path) -> None:
    _write(tmp_path / "tests" / "test_settings.py", 'import os\nvalue = os.getenv("TEST_ONLY_VARIABLE")\n')
    _write(tmp_path / "app.py", 'import os\nvalue = os.getenv("REAL_VARIABLE")\n')

    uses, findings = collect_environment_uses(tmp_path)

    assert findings == []
    assert {item.variable for item in uses} == {"REAL_VARIABLE"}


def test_duplicate_and_invalid_example_lines_are_rejected(tmp_path) -> None:
    example = tmp_path / ".env.example"
    _write(example, "GOOD_NAME=true\nGOOD_NAME=false\ninvalid-name=x\nBROKEN_LINE\n")

    _, findings = parse_env_example(example)
    rules = {item.rule for item in findings}

    assert {"duplicate-env-name", "invalid-env-name", "invalid-env-line"}.issubset(rules)
