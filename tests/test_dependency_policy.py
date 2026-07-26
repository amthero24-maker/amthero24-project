"""Tests for deterministic supply-chain policy enforcement."""
from __future__ import annotations

from pathlib import Path

from scripts.validate_dependency_policy import run_policy, validate_requirements, validate_workflows


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_repository_policy_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    assert run_policy(root) == []


def test_production_manifest_rejects_dev_tools_urls_and_unbounded_ranges(tmp_path) -> None:
    manifest = tmp_path / "requirements.txt"
    _write(
        manifest,
        "pytest>=8,<9\n"
        "unsafe-package>=1\n"
        "remote-package @ https://example.invalid/package.whl\n",
    )

    findings = validate_requirements(manifest, production=True)
    rules = {finding.rule for finding in findings}

    assert "production-dev-tool" in rules
    assert "bounded-range" in rules
    assert "external-source" in rules


def test_auxiliary_manifest_requires_one_base_include(tmp_path) -> None:
    manifest = tmp_path / "requirements-dev.txt"
    _write(manifest, "pytest>=8,<9\n")

    findings = validate_requirements(manifest, production=False, require_base_include=True)

    assert any(finding.rule == "base-include" for finding in findings)


def test_duplicate_dependency_names_are_normalized(tmp_path) -> None:
    manifest = tmp_path / "requirements.txt"
    _write(manifest, "python-dotenv>=1,<2\npython_dotenv>=1,<2\n")

    findings = validate_requirements(manifest, production=True)

    assert any(finding.rule == "duplicate-dependency" for finding in findings)


def test_workflows_reject_dangerous_event_and_floating_action_ref(tmp_path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    _write(
        workflows / "unsafe.yml",
        "on:\n"
        "  pull_request_target:\n"
        "jobs:\n"
        "  unsafe:\n"
        "    steps:\n"
        "      - uses: owner/action@main\n",
    )

    findings = validate_workflows(workflows)
    rules = {finding.rule for finding in findings}

    assert rules == {"pull-request-target", "action-floating"}


def test_workflows_accept_versioned_and_local_actions(tmp_path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    _write(
        workflows / "safe.yml",
        "on: [pull_request]\n"
        "jobs:\n"
        "  safe:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: ./local-action\n",
    )

    assert validate_workflows(workflows) == []
