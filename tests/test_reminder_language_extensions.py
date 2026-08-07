"""Regression coverage for current-turn reminder language selection."""
from __future__ import annotations

from datetime import UTC, datetime

import reminder_language_extensions as language_layer
from data_store import JsonDataStore


def _profile() -> dict[str, object]:
    return {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "session_language": "ar",
    }


def test_current_english_reminder_turn_overrides_arabic_profile() -> None:
    assert language_layer.detect_turn_language(
        "Remind me in 2 minutes to sleep", _profile()
    ) == "en"


def test_english_reminder_title_drops_leftover_infinitive_marker() -> None:
    assert language_layer.clean_english_reminder_title(
        "Remind me in 2 minutes to sleep", "to sleep"
    ) == "sleep"


def test_non_english_reminder_title_is_unchanged() -> None:
    assert language_layer.clean_english_reminder_title(
        "ذكرني بعد دقيقتين نام", "نام"
    ) == "نام"


def test_prepare_turn_language_updates_session_and_consented_preference(tmp_path, monkeypatch) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    store.update_user("49123", _profile())
    monkeypatch.setattr(language_layer.core, "store", store)

    message = language_layer.core.IncomingMessage(
        "wamid-test", "49123", "Remind me in 2 minutes to sleep", "text"
    )
    assert language_layer.prepare_turn_language(message) == "en"

    updated = store.get_user("49123")
    assert updated["session_language"] == "en"
    assert updated["preferred_language"] == "en"
