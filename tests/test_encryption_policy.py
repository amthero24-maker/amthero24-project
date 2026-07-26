"""Security tests for dedicated reminder/support encryption policy."""
from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet

import encryption_policy
import reminder_engine
import support_handoff


STRONG_REMINDER_KEY = "reminder-key-2026-unique-7fA9xQ2mLp8V"
STRONG_SUPPORT_KEY = "support-key-2026-unique-4kT8zN1pRw6M"
STRONG_SUPPORT_TOKEN = "support-api-2026-unique-9qW3eR7tYu2P"


def _cipher(secret: str, value: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key).encrypt(value.encode("utf-8")).decode("ascii")


def test_secret_assessment_rejects_missing_short_and_placeholder_values() -> None:
    assert encryption_policy.assess_secret("X", environment={}).status == "missing"
    assert encryption_policy.assess_secret("X", environment={"X": "short-secret"}).status == "weak"
    assert encryption_policy.assess_secret("X", environment={"X": "a" * 64}).status == "weak"
    assert encryption_policy.assess_secret("X", environment={"X": STRONG_REMINDER_KEY}).status == "configured"


def test_new_reminder_encryption_never_falls_back_to_whatsapp_token(monkeypatch) -> None:
    monkeypatch.delenv("REMINDER_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("WHATSAPP_TOKEN", "whatsapp-token-that-is-long-enough-but-not-an-encryption-key")

    with pytest.raises(reminder_engine.ReminderServiceError, match="reminder_encryption_not_configured"):
        encryption_policy.encrypt_reminder_recipient("491234567")


def test_reminder_round_trip_uses_dedicated_strong_key(monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    monkeypatch.setenv("WHATSAPP_TOKEN", "different-whatsapp-token-for-proof")

    ciphertext = encryption_policy.encrypt_reminder_recipient("491234567")

    assert "491234567" not in ciphertext
    assert encryption_policy.decrypt_reminder_recipient(ciphertext) == "491234567"


def test_historical_whatsapp_token_ciphertext_can_be_read_only_when_enabled(monkeypatch) -> None:
    legacy_token = "historical-whatsapp-token-used-before-dedicated-reminder-key"
    ciphertext = _cipher(legacy_token, "491234567")
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    monkeypatch.setenv("WHATSAPP_TOKEN", legacy_token)
    monkeypatch.setenv("REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED", "true")

    assert encryption_policy.decrypt_reminder_recipient(ciphertext) == "491234567"

    monkeypatch.setenv("REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED", "false")
    with pytest.raises(reminder_engine.ReminderServiceError, match="recipient_decryption_failed"):
        encryption_policy.decrypt_reminder_recipient(ciphertext)


def test_weak_existing_dedicated_key_is_decrypt_only_compatible(monkeypatch) -> None:
    weak_old_key = "old-short-key"
    ciphertext = _cipher(weak_old_key, "491234567")
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", weak_old_key)
    monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)

    assert encryption_policy.reminder_encryption_status() == "weak"
    assert encryption_policy.decrypt_reminder_recipient(ciphertext) == "491234567"
    with pytest.raises(reminder_engine.ReminderServiceError, match="reminder_encryption_not_configured"):
        encryption_policy.encrypt_reminder_recipient("491234567")


def test_support_requires_strong_dedicated_key_and_operator_token(monkeypatch) -> None:
    monkeypatch.setenv("HUMAN_SUPPORT_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_ENCRYPTION_KEY", "weak")
    monkeypatch.setenv("SUPPORT_API_TOKEN", "also-weak")
    assert encryption_policy.support_security_ready() is False

    monkeypatch.setenv("SUPPORT_ENCRYPTION_KEY", STRONG_SUPPORT_KEY)
    monkeypatch.setenv("SUPPORT_API_TOKEN", STRONG_SUPPORT_TOKEN)
    assert encryption_policy.support_security_ready() is True
    ciphertext = encryption_policy.encrypt_support_contact("491234567")
    assert encryption_policy.decrypt_support_contact(ciphertext) == "491234567"


def test_installation_patches_runtime_boundaries(monkeypatch) -> None:
    originals = (
        reminder_engine.encrypt_recipient,
        reminder_engine.decrypt_recipient,
        support_handoff.encrypt_contact,
        support_handoff.decrypt_contact,
        support_handoff.support_configured,
    )
    monkeypatch.setattr(encryption_policy, "_POLICY_INSTALLED", False)
    try:
        encryption_policy.install_encryption_policy()
        assert reminder_engine.encrypt_recipient is encryption_policy.encrypt_reminder_recipient
        assert reminder_engine.decrypt_recipient is encryption_policy.decrypt_reminder_recipient
        assert support_handoff.encrypt_contact is encryption_policy.encrypt_support_contact
        assert support_handoff.decrypt_contact is encryption_policy.decrypt_support_contact
        assert support_handoff.support_configured is encryption_policy.secure_support_configured
    finally:
        (
            reminder_engine.encrypt_recipient,
            reminder_engine.decrypt_recipient,
            support_handoff.encrypt_contact,
            support_handoff.decrypt_contact,
            support_handoff.support_configured,
        ) = originals
        encryption_policy._POLICY_INSTALLED = False
