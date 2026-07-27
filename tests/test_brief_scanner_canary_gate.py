from __future__ import annotations

from types import SimpleNamespace

import groq_client
import prompts
from brief_scanner_app_hook import BriefScannerAppDecision


def _prompt(sender: str, *, has_image: bool = True) -> str:
    return prompts.build_system_prompt(
        sender=sender,
        text="explain",
        detected_language="en",
        profile={},
        history=[],
        has_image=has_image,
    )


def test_canary_marker_requires_exact_normalized_sender(monkeypatch) -> None:
    monkeypatch.setenv("BRIEF_SCANNER_CANARY_SENDERS", "+49 170 1234567,491511112222")

    eligible = _prompt("491701234567")
    partial = _prompt("1701234567")
    unrelated = _prompt("491701234568")

    assert "Brief Scanner canary eligible: true." in eligible
    assert "Brief Scanner canary eligible: false." in partial
    assert "Brief Scanner canary eligible: false." in unrelated


def test_prompt_never_contains_full_sender(monkeypatch) -> None:
    sender = "491701234567"
    monkeypatch.setenv("BRIEF_SCANNER_CANARY_SENDERS", sender)

    prompt = _prompt(sender)

    assert sender not in prompt
    assert "Sender reference: 4567" in prompt
    assert "Brief Scanner canary eligible: true." in prompt


def test_non_image_request_is_never_canary_eligible(monkeypatch) -> None:
    monkeypatch.setenv("BRIEF_SCANNER_CANARY_SENDERS", "491701234567")

    prompt = _prompt("491701234567", has_image=False)

    assert "Brief Scanner canary eligible: false." in prompt


def test_scanner_runs_only_with_explicit_true_marker(monkeypatch) -> None:
    calls = []

    def scanner(**kwargs):
        calls.append(kwargs)
        return BriefScannerAppDecision(use_existing_flow=False, reply="scanner reply")

    monkeypatch.setattr(groq_client, "decide_brief_scanner_media_flow", scanner)

    class MustNotConstruct:
        def __init__(self, **_kwargs):
            raise AssertionError("generic client must not be constructed for handled canary")

    monkeypatch.setattr(groq_client, "Groq", MustNotConstruct)

    system_prompt = (
        "The user's preferred language is en; current reply language is English.\n"
        "- Brief Scanner canary eligible: true."
    )
    reply = groq_client.generate_reply(
        system_prompt=system_prompt,
        user_text="explain",
        image_bytes=b"image",
        mime_type="image/jpeg",
    )

    assert reply == "scanner reply"
    assert len(calls) == 1


def test_false_or_missing_marker_preserves_generic_flow(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    scanner_calls = []

    def scanner(**kwargs):
        scanner_calls.append(kwargs)
        raise AssertionError("scanner must not run")

    monkeypatch.setattr(groq_client, "decide_brief_scanner_media_flow", scanner)

    class FakeCompletions:
        def create(self, **_request):
            message = SimpleNamespace(content="generic reply")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeGroq:
        def __init__(self, *, api_key: str):
            assert api_key == "synthetic-key"
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(groq_client, "Groq", FakeGroq)

    base = "The user's preferred language is en; current reply language is English."
    for system_prompt in (base, base + "\n- Brief Scanner canary eligible: false."):
        reply = groq_client.generate_reply(
            system_prompt=system_prompt,
            user_text="explain",
            image_bytes=b"image",
            mime_type="image/png",
        )
        assert reply == "generic reply"

    assert scanner_calls == []


def test_canary_marker_parser_is_bounded() -> None:
    assert groq_client._brief_scanner_canary_from_prompt("Brief Scanner canary eligible: true.") is True
    assert groq_client._brief_scanner_canary_from_prompt("Brief Scanner canary eligible: false.") is False
    assert groq_client._brief_scanner_canary_from_prompt("Brief Scanner canary eligible: yes.") is False
    assert groq_client._brief_scanner_canary_from_prompt("canary eligible: true.") is False
