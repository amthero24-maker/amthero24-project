"""Atomically re-encrypt reminder recipients with the dedicated production key.

The command is dry-run by default and never prints reminder IDs, phone numbers,
ciphertext, or secret material. Apply mode requires three independent safeguards and
must be run while the bot service is stopped so no reminder delivery races the change.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Iterable

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg.rows import dict_row

from encryption_policy import assess_secret

_APPLY_CONFIRMATION = "REENCRYPT_REMINDERS"
_BOT_STOPPED_CONFIRMATION = "BOT_STOPPED"


@dataclass(frozen=True)
class MigrationReport:
    mode: str
    total: int
    already_current: int
    decryptable_old_key: int
    decryptable_legacy_token: int
    unreadable: int
    migrated: int

    @property
    def safe_to_apply(self) -> bool:
        return self.unreadable == 0

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["safe_to_apply"] = self.safe_to_apply
        return payload


def _database_url(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
    return cleaned


def _enabled(value: str) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _decrypt(ciphertext: str, secret: str) -> str | None:
    try:
        value = _fernet(secret).decrypt(str(ciphertext).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None
    if not 5 <= len(value) <= 32 or any(character not in "+0123456789" for character in value):
        return None
    return value


def _unique_candidates(new_key: str, old_key: str, legacy_token: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen = {new_key}
    for label, value in (("old_key", old_key), ("legacy_token", legacy_token)):
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in seen:
            candidates.append((label, cleaned))
            seen.add(cleaned)
    return candidates


def _table_exists(connection: psycopg.Connection) -> bool:
    row = connection.execute("SELECT to_regclass('hero_reminders') AS table_name").fetchone()
    return bool(row and row["table_name"])


def migrate_reminder_ciphertexts(
    database_url: str,
    *,
    new_key: str,
    old_key: str = "",
    legacy_token: str = "",
    apply: bool = False,
    migration_allowed: bool = False,
    confirmation: str = "",
    bot_stopped_confirmation: str = "",
) -> MigrationReport:
    """Classify or atomically migrate every stored reminder recipient ciphertext."""
    url = _database_url(database_url)
    new_secret = str(new_key or "").strip()
    assessment = assess_secret("NEW_REMINDER_KEY", environment={"NEW_REMINDER_KEY": new_secret})
    if not assessment.ready:
        raise ValueError("The new reminder encryption key is missing or weak")

    if apply:
        if not migration_allowed:
            raise PermissionError("REMINDER_MIGRATION_ALLOWED=true is required")
        if confirmation != _APPLY_CONFIRMATION:
            raise PermissionError(f"confirmation must equal {_APPLY_CONFIRMATION}")
        if bot_stopped_confirmation != _BOT_STOPPED_CONFIRMATION:
            raise PermissionError(f"bot-stopped confirmation must equal {_BOT_STOPPED_CONFIRMATION}")

    candidates = _unique_candidates(new_secret, old_key, legacy_token)
    current_cipher = _fernet(new_secret)
    total = already_current = old_count = legacy_count = unreadable = 0
    replacements: list[tuple[str, str, str]] = []

    with psycopg.connect(url, row_factory=dict_row) as connection:
        if not _table_exists(connection):
            return MigrationReport(
                mode="apply" if apply else "dry-run",
                total=0,
                already_current=0,
                decryptable_old_key=0,
                decryptable_legacy_token=0,
                unreadable=0,
                migrated=0,
            )

        query = "SELECT reminder_id, recipient_ciphertext FROM hero_reminders ORDER BY reminder_id"
        if apply:
            connection.execute("LOCK TABLE hero_reminders IN SHARE ROW EXCLUSIVE MODE")
            query += " FOR UPDATE"
        rows = connection.execute(query).fetchall()
        total = len(rows)

        for row in rows:
            reminder_id = str(row["reminder_id"])
            ciphertext = str(row["recipient_ciphertext"])
            current_plaintext = _decrypt(ciphertext, new_secret)
            if current_plaintext is not None:
                already_current += 1
                continue

            plaintext: str | None = None
            source = ""
            for label, secret in candidates:
                plaintext = _decrypt(ciphertext, secret)
                if plaintext is not None:
                    source = label
                    break
            if plaintext is None:
                unreadable += 1
                continue

            if source == "old_key":
                old_count += 1
            else:
                legacy_count += 1
            replacements.append(
                (
                    current_cipher.encrypt(plaintext.encode("utf-8")).decode("ascii"),
                    reminder_id,
                    ciphertext,
                )
            )

        if apply and unreadable:
            raise RuntimeError(
                "Migration aborted atomically because one or more reminder ciphertexts are unreadable"
            )

        if apply:
            for replacement, reminder_id, original in replacements:
                cursor = connection.execute(
                    """
                    UPDATE hero_reminders
                    SET recipient_ciphertext = %s, updated_at = NOW()
                    WHERE reminder_id = %s AND recipient_ciphertext = %s
                    """,
                    (replacement, reminder_id, original),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Migration aborted because reminder data changed concurrently")

            verification_rows = connection.execute(
                "SELECT recipient_ciphertext FROM hero_reminders"
            ).fetchall()
            if any(_decrypt(str(row["recipient_ciphertext"]), new_secret) is None for row in verification_rows):
                raise RuntimeError("Migration verification failed; transaction rolled back")

    return MigrationReport(
        mode="apply" if apply else "dry-run",
        total=total,
        already_current=already_current,
        decryptable_old_key=old_count,
        decryptable_legacy_token=legacy_count,
        unreadable=unreadable,
        migrated=len(replacements) if apply else 0,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run or apply reminder recipient re-encryption.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--new-key", default=os.getenv("REMINDER_ENCRYPTION_KEY", ""))
    parser.add_argument("--old-key", default=os.getenv("REMINDER_OLD_ENCRYPTION_KEY", ""))
    parser.add_argument(
        "--legacy-whatsapp-token",
        default=os.getenv("REMINDER_LEGACY_WHATSAPP_TOKEN", ""),
    )
    parser.add_argument(
        "--use-current-whatsapp-token",
        action="store_true",
        help="Use the current WHATSAPP_TOKEN only as a legacy decrypt candidate.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--confirm-bot-stopped", default="")
    args = parser.parse_args(argv)

    legacy_token = str(args.legacy_whatsapp_token or "").strip()
    if args.use_current_whatsapp_token:
        legacy_token = os.getenv("WHATSAPP_TOKEN", "").strip()

    try:
        report = migrate_reminder_ciphertexts(
            args.database_url,
            new_key=args.new_key,
            old_key=args.old_key,
            legacy_token=legacy_token,
            apply=bool(args.apply),
            migration_allowed=_enabled(os.getenv("REMINDER_MIGRATION_ALLOWED", "false")),
            confirmation=str(args.confirm or ""),
            bot_stopped_confirmation=str(args.confirm_bot_stopped or ""),
        )
    except (ValueError, PermissionError, RuntimeError, psycopg.Error, OSError) as exc:
        print(f"Reminder migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report.as_dict(), sort_keys=True))
    return 0 if report.safe_to_apply else 2


if __name__ == "__main__":
    sys.exit(main())
