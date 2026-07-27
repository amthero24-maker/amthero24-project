"""Real PostgreSQL integration tests for the complete production composition.

This module runs only in the dedicated CI job with an ephemeral PostgreSQL service.
It never connects to Railway or any shared database.
"""
from __future__ import annotations

import base64
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

import webhook_security
from abuse_guard import AbuseGuardRepository
from data_store import PostgresDataStore
from document_action_repository import PendingDocumentRepository
from encryption_policy import decrypt_reminder_recipient
from entitlement_engine import EntitlementRepository
from hero_memory import HeroMemory
from reminder_engine import ReminderRepository
from scripts.migrate_reminder_encryption import migrate_reminder_ciphertexts
from support_handoff import SupportRepository


EXPECTED_TABLES = {
    "hero_users",
    "inbound_messages",
    "schema_migrations",
    "hero_missions",
    "memory_consent_events",
    "hero_reminders",
    "pending_document_actions",
    "hero_entitlements",
    "hero_usage_counters",
    "abuse_rate_windows",
    "abuse_blocks",
    "abuse_guard_events",
    "provider_operational_events",
    "provider_circuit_state",
    "human_support_tickets",
    "human_support_admin_events",
    "anonymous_feedback",
}


def _store():
    import runtime_health

    assert runtime_health.store.backend_name == "postgresql"
    return runtime_health.store


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in phone if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _legacy_cipher(secret: str, phone: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key).encrypt(phone.encode("utf-8")).decode("ascii")


@pytest.fixture(autouse=True)
def clean_linked_records() -> None:
    store = _store()
    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
        tables = {str(row["tablename"]) for row in rows}
        truncatable = sorted(tables & EXPECTED_TABLES)
        if truncatable:
            connection.execute("TRUNCATE " + ", ".join(truncatable) + " RESTART IDENTITY")
    yield
    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
        tables = {str(row["tablename"]) for row in rows}
        truncatable = sorted(tables & EXPECTED_TABLES)
        if truncatable:
            connection.execute("TRUNCATE " + ", ".join(truncatable) + " RESTART IDENTITY")


def test_full_production_startup_creates_schemas_and_admin_endpoints_work() -> None:
    store = _store()
    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
    tables = {str(row["tablename"]) for row in rows}
    assert EXPECTED_TABLES <= tables

    with TestClient(webhook_security.app) as client:
        ready = client.get("/ready")
        overview = client.get(
            "/admin/overview",
            headers={"Authorization": f"Bearer {os.environ['ADMIN_API_TOKEN']}"},
        )
        launch = client.get(
            "/admin/launch-readiness",
            headers={"Authorization": f"Bearer {os.environ['ADMIN_API_TOKEN']}"},
        )

    assert ready.status_code == 200
    assert ready.json()["components"]["storage_backend"] == "postgresql"
    assert ready.json()["components"]["postgresql_schemas"] == "initialized"
    assert ready.json()["components"]["webhook_signature"] == "enforced"
    assert overview.status_code == 200
    assert overview.json()["storage_backend"] == "postgresql"
    assert launch.status_code == 200
    assert launch.json()["status"] in {"ready", "warning"}


def test_cross_replica_message_dedupe_and_profile_persistence() -> None:
    database_url = os.environ["DATABASE_URL"]
    first = PostgresDataStore(database_url)
    second = PostgresDataStore(database_url)
    phone = "+49157" + uuid4().hex[:8]
    message_id = "wamid." + uuid4().hex

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda store: store.claim_message(message_id, phone, "integration message"),
                    (first, second),
                )
            )
        assert sorted(results) == [False, True]

        first.update_user(phone, {"first_name": "Integration", "preferred_language": "de"})
        assert second.get_user(phone)["first_name"] == "Integration"
        assert second.recent_user_messages(phone) == ["integration message"]

        with first.pool.connection() as connection:
            row = connection.execute(
                "SELECT phone_hash FROM hero_users WHERE phone_hash = %s",
                (_phone_hash(phone),),
            ).fetchone()
        assert row is not None
        assert row["phone_hash"] != phone
    finally:
        first.delete_user(phone)
        first.close()
        second.close()


def test_cross_replica_mission_creation_is_idempotent() -> None:
    database_url = os.environ["DATABASE_URL"]
    first_store = PostgresDataStore(database_url)
    second_store = PostgresDataStore(database_url)
    phone = "+49158" + uuid4().hex[:8]
    idempotency_key = hashlib.sha256(uuid4().bytes).hexdigest()
    arguments = {
        "title": "Track document deadline",
        "topic": "document",
        "next_step": "Complete the required action before the deadline",
        "due_at": "2026-09-01",
        "metadata": {
            "source": "brief_scanner",
            "category": "track_deadline",
        },
        "idempotency_key": idempotency_key,
    }

    try:
        memories = (HeroMemory(first_store), HeroMemory(second_store))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda memory: memory.create_mission(phone, **arguments),
                    memories,
                )
            )

        assert sorted(result["_operation"] for result in results) == [
            "created",
            "replayed",
        ]
        assert len({str(result["mission_id"]) for result in results}) == 1
        assert len(memories[0].list_missions(phone, status="all")) == 1

        with pytest.raises(ValueError, match="mission_idempotency_conflict"):
            memories[1].create_mission(
                phone,
                **{**arguments, "title": "Changed after authorization"},
            )
    finally:
        first_store.close()
        second_store.close()


def test_complete_user_deletion_removes_every_linked_postgres_layer() -> None:
    store = _store()
    phone = "+49160" + uuid4().hex[:8]
    key = _phone_hash(phone)
    message_id = "wamid." + uuid4().hex
    now = datetime.now(UTC)

    memory = HeroMemory(store)
    reminders = ReminderRepository(store)
    pending = PendingDocumentRepository(store)
    entitlements = EntitlementRepository(store)
    abuse = AbuseGuardRepository(store)
    support = SupportRepository(store)

    store.update_user(phone, {
        "first_name": "DeleteMe",
        "preferred_language": "ar",
        "memory_consent": "granted",
        "onboarding_stage": "complete",
    })
    store.claim_message(message_id, phone, "temporary content")
    memory.record_consent(phone, "granted", "integration-v1")
    mission = memory.create_mission(phone, title="Integration mission", topic="general")
    reminders.create(
        phone,
        title="Integration reminder",
        scheduled_at=now + timedelta(days=1),
        language="ar",
        mission_id=str(mission["mission_id"]),
    )
    pending.put(phone, {"title": "Pending document", "topic": "document", "next_step": "Review"})
    entitlements.set_plan(phone, "hero", source="integration")
    entitlements.check_and_consume(phone, "documents_monthly", now=now)
    abuse.check(phone, now=now)
    support.create(phone, language="ar", category="general", urgency="normal")

    assert memory.delete_all_user_data(phone) is True

    linked_tables = (
        "hero_users",
        "inbound_messages",
        "hero_missions",
        "memory_consent_events",
        "hero_reminders",
        "pending_document_actions",
        "hero_entitlements",
        "hero_usage_counters",
        "abuse_rate_windows",
        "abuse_blocks",
        "human_support_tickets",
    )
    with store.pool.connection() as connection:
        for table in linked_tables:
            count = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE phone_hash = %s",
                (key,),
            ).fetchone()
            assert int(count["count"]) == 0, table

        privacy_event = connection.execute(
            "SELECT COUNT(*) AS count FROM privacy_deletion_events"
        ).fetchone()
    assert int(privacy_event["count"]) == 1


def test_reminder_ciphertext_migration_is_atomic_and_idempotent() -> None:
    store = _store()
    database_url = os.environ["DATABASE_URL"]
    new_key = os.environ["REMINDER_ENCRYPTION_KEY"]
    old_key = "old-dedicated-reminder-key-before-rotation"
    legacy_token = "historical-whatsapp-token-before-key-separation"
    now = datetime.now(UTC)
    current_phone = "+491701111111"
    old_phone = "+491702222222"
    legacy_phone = "+491703333333"

    ReminderRepository(store).create(
        current_phone,
        title="Current key",
        scheduled_at=now + timedelta(days=1),
        language="de",
    )
    with store.pool.connection() as connection:
        for label, phone, ciphertext in (
            ("old", old_phone, _legacy_cipher(old_key, old_phone)),
            ("legacy", legacy_phone, _legacy_cipher(legacy_token, legacy_phone)),
        ):
            connection.execute(
                """
                INSERT INTO hero_reminders
                    (reminder_id, dedupe_key, phone_hash, recipient_ciphertext, title,
                     language, timezone, scheduled_at, next_attempt_at)
                VALUES (%s, %s, %s, %s, %s, 'de', 'Europe/Berlin', %s, %s)
                """,
                (
                    f"migration-{label}",
                    hashlib.sha256(f"migration-{label}".encode()).hexdigest(),
                    _phone_hash(phone),
                    ciphertext,
                    f"Migration {label}",
                    now + timedelta(days=2),
                    now + timedelta(days=2),
                ),
            )

    dry_run = migrate_reminder_ciphertexts(
        database_url,
        new_key=new_key,
        old_key=old_key,
        legacy_token=legacy_token,
    )
    assert dry_run.total == 3
    assert dry_run.already_current == 1
    assert dry_run.decryptable_old_key == 1
    assert dry_run.decryptable_legacy_token == 1
    assert dry_run.unreadable == 0
    assert dry_run.migrated == 0

    applied = migrate_reminder_ciphertexts(
        database_url,
        new_key=new_key,
        old_key=old_key,
        legacy_token=legacy_token,
        apply=True,
        migration_allowed=True,
        confirmation="REENCRYPT_REMINDERS",
        bot_stopped_confirmation="BOT_STOPPED",
    )
    assert applied.migrated == 2
    assert applied.unreadable == 0

    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT recipient_ciphertext FROM hero_reminders ORDER BY reminder_id"
        ).fetchall()
    decrypted = {decrypt_reminder_recipient(str(row["recipient_ciphertext"])) for row in rows}
    assert decrypted == {current_phone, old_phone, legacy_phone}

    second_run = migrate_reminder_ciphertexts(
        database_url,
        new_key=new_key,
        old_key=old_key,
        legacy_token=legacy_token,
    )
    assert second_run.total == 3
    assert second_run.already_current == 3
    assert second_run.decryptable_old_key == 0
    assert second_run.decryptable_legacy_token == 0
    assert second_run.unreadable == 0
