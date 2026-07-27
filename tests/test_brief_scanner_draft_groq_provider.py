from __future__ import annotations

import json
import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from brief_scanner_draft_boundary import BriefScannerDraftBoundaryStatus
from brief_scanner_draft_groq_provider import (
    generate_brief_scanner_draft_with_groq,
)
from brief_scanner_draft_planner import BriefScannerDraftKind
from brief_scanner_execution_boundary import (
    BriefScannerDraftCommand,
    BriefScannerExecutionCommandKind,
)


class _FakeCompletions:
    def __init__(self, content: object, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.request = None

    def create(self, **request):
        self.request = request
        if self.fail:
            raise RuntimeError("sensitive-provider-request-marker")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.content)),
            ]
        )


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _factory(completions: _FakeCompletions):
    def create_client(*, api_key: str):
        assert api_key == "synthetic-key"
        return _FakeClient(completions)

    return create_client


def _command() -> BriefScannerDraftCommand:
    return BriefScannerDraftCommand(
        kind=BriefScannerExecutionCommandKind.GENERATE_DRAFT,
        draft_kind=BriefScannerDraftKind.FORMAL_RESPONSE,
        recipient_organization="Synthetic Authority",
        response_instruction="Ask for a two-week extension.",
        document_requested_action="Send the requested documents.",
        source_language="en",
        output_language="de",
        due_date=date(2026, 9, 1),
        reference_number="SYNTHETIC-REF-001",
        contact_channel_hint="email",
    )


def _valid_output() -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "language": "de",
            "translated_instruction": (
                "Ich bitte höflich um eine Verlängerung der Frist um zwei Wochen."
            ),
            "uncertainty": "",
        }
    )


def test_disabled_or_invalid_flag_never_calls_provider() -> None:
    called = False

    def forbidden(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must remain unreachable")

    disabled = generate_brief_scanner_draft_with_groq(
        _command(),
        client_factory=forbidden,
    )
    invalid = generate_brief_scanner_draft_with_groq(
        _command(),
        client_factory=forbidden,
        enabled=1,  # type: ignore[arg-type]
    )

    assert disabled.error_code == "brief_scanner_draft_provider_disabled"
    assert invalid.error_code == "brief_scanner_draft_provider_flag_invalid"
    assert called is False


def test_enabled_provider_uses_zero_temperature_and_strict_boundary(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    completions = _FakeCompletions(_valid_output())

    with patch(
        "brief_scanner_draft_groq_provider.GROQ_MODEL",
        "openai/gpt-oss-120b",
    ):
        outcome = generate_brief_scanner_draft_with_groq(
            _command(),
            client_factory=_factory(completions),
            enabled=True,
        )

    assert outcome.status is BriefScannerDraftBoundaryStatus.VALIDATED
    request = completions.request
    assert request["temperature"] == 0
    assert request["max_tokens"] == 600
    assert request["include_reasoning"] is False
    assert request["reasoning_effort"] == "low"
    assert "Ask for a two-week extension." in request["messages"][0]["content"]
    assert "Synthetic Authority" not in request["messages"][0]["content"]
    assert "SYNTHETIC-REF-001" not in request["messages"][0]["content"]


def test_provider_failure_is_sanitized_and_logs_no_request_content(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    caplog.set_level(logging.ERROR)
    completions = _FakeCompletions("", fail=True)

    outcome = generate_brief_scanner_draft_with_groq(
        _command(),
        client_factory=_factory(completions),
        enabled=True,
    )

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert outcome.error_code == "brief_scanner_draft_provider_request_failed"
    assert logged == "Brief Scanner draft provider request failed"
    assert "sensitive-provider-request-marker" not in logged
    assert "SYNTHETIC-REF-001" not in logged


def test_malformed_provider_output_cannot_render(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    outcome = generate_brief_scanner_draft_with_groq(
        _command(),
        client_factory=_factory(_FakeCompletions("not-json")),
        enabled=True,
    )

    assert outcome.status is BriefScannerDraftBoundaryStatus.RETRYABLE_MODEL_OUTPUT
    assert outcome.allows_rendering is False
