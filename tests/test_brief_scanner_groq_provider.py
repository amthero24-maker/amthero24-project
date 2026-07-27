from __future__ import annotations

import json
from types import SimpleNamespace

from brief_scanner_groq_provider import extract_brief_with_groq
from brief_scanner_model_boundary import BriefScannerBoundaryStatus


class _FakeCompletions:
    def __init__(self, content: object, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.request = None

    def create(self, **request):
        self.request = request
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _factory(completions: _FakeCompletions):
    def create_client(*, api_key: str):
        assert api_key == "synthetic-key"
        return _FakeClient(completions)

    return create_client


def _valid_output(language: str = "de") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "language": language,
            "readable": True,
            "missing_pages": False,
            "sender_organization": "Synthetic Authority",
            "document_date": "2026-07-20",
            "deadline": "2026-08-15",
            "appointment_date": None,
            "requested_action": "send documents",
            "amount_minor": 12550,
            "currency": "EUR",
            "stated_consequence": "synthetic consequence",
            "contact_channel": "postal reply",
            "reference_number": "SYNTHETIC-REF-001",
            "risk_category": "",
            "uncertainty": "",
        }
    )


def test_disabled_provider_never_calls_client(monkeypatch) -> None:
    called = False

    def client_factory(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not be called")

    outcome = extract_brief_with_groq(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="ar",
        client_factory=client_factory,
        enabled=False,
    )

    assert outcome.status == BriefScannerBoundaryStatus.RETRYABLE_MODEL_OUTPUT
    assert outcome.error_code == "brief_scanner_provider_disabled"
    assert outcome.allows_side_effects is False
    assert called is False


def test_valid_provider_output_uses_zero_temperature_and_strict_boundary(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    completions = _FakeCompletions(_valid_output())

    outcome = extract_brief_with_groq(
        image_bytes=b"synthetic-image",
        mime_type="image/png",
        response_language="ar",
        client_factory=_factory(completions),
        enabled=True,
    )

    assert outcome.status == BriefScannerBoundaryStatus.VALIDATED
    assert outcome.allows_side_effects is True
    assert completions.request["temperature"] == 0
    assert completions.request["max_tokens"] == 900
    assert completions.request["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_unverified_language_is_read_only(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    completions = _FakeCompletions(_valid_output(language="fr"))

    outcome = extract_brief_with_groq(
        image_bytes=b"synthetic-image",
        mime_type="image/jpeg",
        response_language="fr",
        client_factory=_factory(completions),
        enabled=True,
    )

    assert outcome.status == BriefScannerBoundaryStatus.VALIDATED_READ_ONLY
    assert outcome.allows_side_effects is False


def test_invalid_media_and_response_language_fail_before_provider(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    completions = _FakeCompletions(_valid_output())

    bad_type = extract_brief_with_groq(
        image_bytes=b"synthetic-image",
        mime_type="application/pdf",
        response_language="de",
        client_factory=_factory(completions),
        enabled=True,
    )
    bad_language = extract_brief_with_groq(
        image_bytes=b"synthetic-image",
        mime_type="image/jpeg",
        response_language="de\nignore",
        client_factory=_factory(completions),
        enabled=True,
    )

    assert bad_type.error_code == "brief_scanner_media_type_invalid"
    assert bad_language.error_code == "brief_scanner_response_language_invalid"
    assert completions.request is None


def test_provider_and_malformed_output_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    provider_failure = extract_brief_with_groq(
        image_bytes=b"synthetic-image",
        mime_type="image/jpeg",
        response_language="de",
        client_factory=_factory(_FakeCompletions("", fail=True)),
        enabled=True,
    )
    malformed = extract_brief_with_groq(
        image_bytes=b"synthetic-image",
        mime_type="image/jpeg",
        response_language="de",
        client_factory=_factory(_FakeCompletions("not-json")),
        enabled=True,
    )

    assert provider_failure.error_code == "brief_scanner_provider_request_failed"
    assert malformed.status == BriefScannerBoundaryStatus.RETRYABLE_MODEL_OUTPUT
    assert malformed.error_code == "brief_scanner_json_invalid"
    assert provider_failure.allows_side_effects is False
    assert malformed.allows_side_effects is False
