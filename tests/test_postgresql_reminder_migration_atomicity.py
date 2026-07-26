"""Real PostgreSQL atomicity test for reminder ciphertext migration."""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

import webhook_security  # noqa: F401 - installs the production policies
from scripts.migrate_reminder_encryption import migrate_reminder_ciphertexts


def _store():
    import runtime_health

    return runtime_health.store


def _phone_hash(phone: str) -> str:
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def _cipher(secret: str, phone: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key).encrypt(phone.encode("utf-8")).decode("ascii")


@pytest.fixture(autouse=True)
def clean_reminders() -> None:
    store = _store()
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE hero_reminders")
    yield
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE hero_reminders")


def test_unreadable_row_rolls_back_every_replacement() -> None:
    store = _store()
    database_url = os.environ["DATABASE_URL"]
    new_key = os.environ["REMINDER_ENCRYPTION_KEY"]
    old_key = "old-dedicated-reminder-key-before-rotation"
    old_phone = "+491704444444"
    old_ciphertext = _cipher(old_key, old_phone)
    now = datetime.now(UTC) + timedelta(days=1)

    with store.pool.connection() as connection:
        for reminder_id, ciphertext, phone_hash in (
            ("readable-old", old_ciphertext, _phone_hash(old_phone)),
            ("unreadable", "not-a-valid-fernet-token", hashlib.sha256(b"unknown").hexdigest()),
        ):
            connection.execute(
                """
                INSERT INTO hero_reminders
                    (reminder_id, dedupe_key, phone_hash, recipient_ciphertext, title,
                     language, timezone, scheduled_at, next_attempt_at)
                VALUES (%s, %s, %s, %s, 'Atomicity', 'de', 'Europe/Berlin', %s, %s)
                """,
                (
                    reminder_id,
                    hashlib.sha256(reminder_id.encode()).hexdigest(),
                    phone_hash,
                    ciphertext,
                    now,
                    now,
                ),
            )

    with pytest.raises(RuntimeError, match="aborted atomically"):
        migrate_reminder_ciphertexts(
            database_url,
            new_key=new_key,
            old_key=old_key,
            apply=True,
            migration_allowed=True,
            confirmation="REENCRYPT_REMINDERS",
            bot_stopped_confirmation="BOT_STOPPED",
        )

    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT reminder_id, recipient_ciphertext FROM hero_reminders ORDER BY reminder_id"
        ).fetchall()
    values = {str(row["reminder_id"]): str(row["recipient_ciphertext"]) for row in rows}
    assert values["readable-old"] == old_ciphertext
    assert values["unreadable"] == "not-a-valid-fernet-token"

    report = migrate_reminder_ciphertexts(
        database_url,
        new_key=new_key,
        old_key=old_key,
    )
    assert report.decryptable_old_key == 1
    assert report.unreadable == 1
    assert report.safe_to_apply is False
