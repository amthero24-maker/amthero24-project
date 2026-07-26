"""Unit safety tests for reminder recipient re-encryption."""
from __future__ import annotations

import pytest

from scripts.migrate_reminder_encryption import migrate_reminder_ciphertexts

DATABASE_URL = "postgresql://db.internal/amthero24"
STRONG_NEW_KEY = "reminder-new-2026-unique-7fA9xQ2mLp8V"


def test_migration_rejects_weak_new_key_before_connecting() -> None:
    with pytest.raises(ValueError, match="missing or weak"):
        migrate_reminder_ciphertexts(DATABASE_URL, new_key="weak")


def test_apply_requires_feature_flag_before_connecting() -> None:
    with pytest.raises(PermissionError, match="REMINDER_MIGRATION_ALLOWED"):
        migrate_reminder_ciphertexts(
            DATABASE_URL,
            new_key=STRONG_NEW_KEY,
            apply=True,
            migration_allowed=False,
            confirmation="REENCRYPT_REMINDERS",
            bot_stopped_confirmation="BOT_STOPPED",
        )


def test_apply_requires_exact_operation_confirmation_before_connecting() -> None:
    with pytest.raises(PermissionError, match="confirmation"):
        migrate_reminder_ciphertexts(
            DATABASE_URL,
            new_key=STRONG_NEW_KEY,
            apply=True,
            migration_allowed=True,
            confirmation="wrong",
            bot_stopped_confirmation="BOT_STOPPED",
        )


def test_apply_requires_bot_stopped_confirmation_before_connecting() -> None:
    with pytest.raises(PermissionError, match="bot-stopped"):
        migrate_reminder_ciphertexts(
            DATABASE_URL,
            new_key=STRONG_NEW_KEY,
            apply=True,
            migration_allowed=True,
            confirmation="REENCRYPT_REMINDERS",
            bot_stopped_confirmation="wrong",
        )
