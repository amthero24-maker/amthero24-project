from __future__ import annotations

from types import SimpleNamespace

import groq_client
from brief_scanner_app_hook import BriefScannerAppDecision


class _FakeCompletions:
    def __init__(self, reply: str = "generic reply") -> None:
        self.reply = reply
        self.calls = 0
        self.request = None

    def create(self, **request):
        self.calls += 1
        self.request = request
        message = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeGroq:
    completions = _FakeCompletions()

    def __init__(self, *, api_key: str) -> None:
        assert api_key == "synthetic-key"
        self.chat = SimpleNamespace(completions=self.completions)


def _prompt(language_name: str) -> str:
    return (
        "The user's preferred language is de; "
        f"current reply language is {language_name}. "
        "Brief Scanner canary eligible: true."
    )


def test_handled_scanner_reply_bypasses_generic_groq(monkeypatch) -> None:
    monkeypatch.setattr(
        groq_client,
        "decide_brief_scanner_media_flow",
        lambda **kwargs: BriefScannerAppDecision(
            use_existing_flow=False,
            reply="Document overview:\nDeadline: 2026-08-15",
        ),
    )

    class _MustNotConstruct:
        def __init__(self, **_kwargs) -> None:
            raise AssertionError("generic Groq client must not be created")

    monkeypatch.setattr(groq_client, "Groq", _MustNotConstruct)

    reply = groq_client.generate_reply(
        system_prompt=_prompt("English"),
        user_text="explain",
        image_bytes=b"synthetic-image",
        mime_type="image/jpeg",
    )

    assert reply == "Document overview:\nDeadline: 2026-08-15"


def test_scanner_fallback_preserves_existing_vision_flow(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    completions = _FakeCompletions("generic vision reply")
    _FakeGroq.completions = completions
    monkeypatch.setattr(groq_client, "Groq", _FakeGroq)
    monkeypatch.setattr(
        groq_client,
        "decide_brief_scanner_media_flow",
        lambda **kwargs: BriefScannerAppDecision(use_existing_flow=True),
    )

    reply = groq_client.generate_reply(
        system_prompt=_prompt("Arabic"),
        user_text="اشرح الصورة",
        image_bytes=b"synthetic-image",
        mime_type="image/png",
    )

    assert reply == "generic vision reply"
    assert completions.calls == 1
    assert completions.request["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_missing_language_marker_skips_scanner_and_preserves_generic_flow(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    completions = _FakeCompletions("legacy reply")
    _FakeGroq.completions = completions
    monkeypatch.setattr(groq_client, "Groq", _FakeGroq)

    scanner_called = False

    def scanner(**_kwargs):
        nonlocal scanner_called
        scanner_called = True
        raise AssertionError("scanner must not run without deterministic language marker")

    monkeypatch.setattr(groq_client, "decide_brief_scanner_media_flow", scanner)

    reply = groq_client.generate_reply(
        system_prompt="legacy prompt without reply language metadata",
        user_text="explain",
        image_bytes=b"synthetic-image",
        mime_type="image/webp",
    )

    assert reply == "legacy reply"
    assert scanner_called is False
    assert completions.calls == 1


def test_language_marker_mapping_is_bounded() -> None:
    assert groq_client._response_language_from_prompt(_prompt("German")) == "de"
    assert groq_client._response_language_from_prompt(_prompt("Arabic")) == "ar"
    assert groq_client._response_language_from_prompt(_prompt("English")) == "en"
    assert groq_client._response_language_from_prompt(_prompt("Ukrainian")) == "uk"
    assert groq_client._response_language_from_prompt(_prompt("Greek")) == "el"
    assert groq_client._response_language_from_prompt(_prompt("French")) is None
    assert groq_client._response_language_from_prompt("") is None
