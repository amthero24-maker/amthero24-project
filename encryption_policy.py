"""Central security policy for reversible contact encryption and operator secrets.

New reminder/support ciphertext must use dedicated strong keys. Reminder decryption
keeps a temporary, explicit legacy path so existing rows created with the historical
WhatsApp-token fallback can be migrated without losing scheduled reminders.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken

_MIN_SECRET_LENGTH = 32
_COMMON_PLACEHOLDERS = {
    "changeme",
    "change-me",
    "secret",
    "password",
    "test-secret",
    "your-secret",
    "your_token_here",
    "configured",
}
_POLICY_INSTALLED = False


@dataclass(frozen=True)
class SecretAssessment:
    name: str
    status: str
    minimum_length: int = _MIN_SECRET_LENGTH

    @property
    def ready(self) -> bool:
        return self.status == "configured"


def _flag(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().casefold() in {"1", "true", "yes", "on"}


def assess_secret(name: str, *, minimum_length: int = _MIN_SECRET_LENGTH) -> SecretAssessment:
    """Classify one secret without returning its value or any derived material."""
    value = os.getenv(name, "").strip()
    if not value:
        return SecretAssessment(name, "missing", minimum_length)
    normalized = value.casefold()
    unique_characters = len(set(value))
    placeholder = normalized in _COMMON_PLACEHOLDERS or normalized.startswith(("test-", "example-", "integration-"))
    repeated = bool(re.fullmatch(r"(.)\1+", value))
    if len(value) < minimum_length or unique_characters < 8 or placeholder or repeated:
        return SecretAssessment(name, "weak", minimum_length)
    return SecretAssessment(name, "configured", minimum_length)


def reminder_encryption_status() -> str:
    return assess_secret("REMINDER_ENCRYPTION_KEY").status


def reminder_encryption_ready() -> bool:
    return reminder_encryption_status() == "configured"


def legacy_reminder_decryption_enabled() -> bool:
    return _flag("REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED", True)


def support_encryption_status() -> str:
    return assess_secret("SUPPORT_ENCRYPTION_KEY").status


def support_api_token_status() -> str:
    return assess_secret("SUPPORT_API_TOKEN").status


def admin_api_token_status() -> str:
    return assess_secret("ADMIN_API_TOKEN").status


def support_security_ready() -> bool:
    return support_encryption_status() == "configured" and support_api_token_status() == "configured"


def _fernet_from_secret(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _required_strong_secret(name: str, error_type: type[RuntimeError], error_code: str) -> str:
    assessment = assess_secret(name)
    if not assessment.ready:
        raise error_type(error_code)
    return os.getenv(name, "").strip()


def encrypt_reminder_recipient(phone: str) -> str:
    """Encrypt new reminder recipients only with the dedicated strong key."""
    import reminder_engine

    if not phone:
        raise reminder_engine.ReminderServiceError("missing_recipient")
    secret = _required_strong_secret(
        "REMINDER_ENCRYPTION_KEY",
        reminder_engine.ReminderServiceError,
        "reminder_encryption_not_configured",
    )
    return _fernet_from_secret(secret).encrypt(phone.encode("utf-8")).decode("ascii")


def decrypt_reminder_recipient(ciphertext: str) -> str:
    """Decrypt with the dedicated key, then optionally the historical token key."""
    import reminder_engine

    candidates: list[str] = []
    dedicated = os.getenv("REMINDER_ENCRYPTION_KEY", "").strip()
    if dedicated:
        candidates.append(dedicated)
    if legacy_reminder_decryption_enabled():
        legacy = os.getenv("WHATSAPP_TOKEN", "").strip()
        if legacy and legacy not in candidates:
            candidates.append(legacy)

    for secret in candidates:
        try:
            return _fernet_from_secret(secret).decrypt(str(ciphertext).encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            continue
    raise reminder_engine.ReminderServiceError("recipient_decryption_failed")


def encrypt_support_contact(phone: str) -> str:
    import support_handoff

    if not phone:
        raise support_handoff.SupportServiceError("missing_contact")
    secret = _required_strong_secret(
        "SUPPORT_ENCRYPTION_KEY",
        support_handoff.SupportServiceError,
        "support_encryption_not_configured",
    )
    return _fernet_from_secret(secret).encrypt(phone.encode("utf-8")).decode("ascii")


def decrypt_support_contact(ciphertext: str) -> str:
    import support_handoff

    secret = _required_strong_secret(
        "SUPPORT_ENCRYPTION_KEY",
        support_handoff.SupportServiceError,
        "support_encryption_not_configured",
    )
    try:
        return _fernet_from_secret(secret).decrypt(str(ciphertext).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise support_handoff.SupportServiceError("contact_decryption_failed") from exc


def secure_support_configured() -> bool:
    import support_handoff

    return support_handoff.support_enabled() and support_security_ready()


def install_encryption_policy() -> None:
    """Patch reversible encryption boundaries before production composition imports."""
    global _POLICY_INSTALLED
    if _POLICY_INSTALLED:
        return

    import reminder_engine
    import support_handoff

    reminder_engine.encrypt_recipient = encrypt_reminder_recipient
    reminder_engine.decrypt_recipient = decrypt_reminder_recipient
    support_handoff.encrypt_contact = encrypt_support_contact
    support_handoff.decrypt_contact = decrypt_support_contact
    support_handoff.support_configured = secure_support_configured
    _POLICY_INSTALLED = True
