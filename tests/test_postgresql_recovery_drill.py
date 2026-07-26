"""End-to-end backup and restore drill against two ephemeral PostgreSQL databases.

The CI job uses an isolated service container. No Railway database, production secret,
or real user data is accessed.
"""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from cryptography.fernet import Fernet
from psycopg.rows import dict_row

from abuse_guard import AbuseGuardRepository
from data_store import PostgresDataStore
from document_action_repository import PendingDocumentRepository
from durable_queue import DurableQueueRepository
from entitlement_engine import EntitlementRepository
from feedback_engine import FeedbackRepository
from hero_memory import HeroMemory
from outbound_delivery import DeliveryReceipt, OutboundDeliveryRepository
from provider_reliability import ProviderReliabilityRepository
from reminder_engine import ReminderRepository, decrypt_recipient
from schema_bootstrap import bootstrap_postgres_schemas
from scripts.postgres_backup import create_backup
from scripts.postgres_restore import restore_backup
from support_handoff import SupportRepository


PHONE = "+4915712345678"
PHONE_HASH = hashlib.sha256(PHONE.encode("utf-8")).hexdigest()
OUTBOUND_ID = "wamid.recovery-outbound"
EXPECTED_TABLES = {
    "hero_users",
    "inbound_messages",
    "inbound_work_queue",
    "outbound_delivery_messages",
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
COUNTED_TABLES = EXPECTED_TABLES - {
    "schema_migrations", "abuse_blocks", "human_support_admin_events", "provider_circuit_state"
}


def _reset_target(admin_url: str, target_name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{target_name}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{target_name}"')


def _table_names(database_url: str) -> set[str]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
    return {str(row["tablename"]) for row in rows}


def _counts(database_url: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        available = _table_names(database_url)
        for table in sorted(COUNTED_TABLES & available):
            row = connection.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()
            counts[table] = int(row["count"])
    return counts


def _seed_source(database_url: str) -> dict[str, str]:
    store = PostgresDataStore(database_url)
    bootstrap_postgres_schemas(store)
    memory = HeroMemory(store)
    reminders = ReminderRepository(store)
    pending = PendingDocumentRepository(store)
    entitlements = EntitlementRepository(store)
    abuse = AbuseGuardRepository(store)
    provider = ProviderReliabilityRepository(store)
    support = SupportRepository(store)
    feedback = FeedbackRepository(store)
    durable_queue = DurableQueueRepository(store)
    outbound = OutboundDeliveryRepository(store)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)

    try:
        store.update_user(PHONE, {
            "first_name": "RecoveryTest",
            "preferred_language": "ar",
            "memory_consent": "granted",
            "onboarding_stage": "complete",
            "current_topic": "document",
        })
        store.claim_message("wamid.recovery-drill", PHONE, "recovery test message")
        store.update_message_status("wamid.recovery-drill", "sent")
        durable_queue.enqueue("wamid.recovery-drill", PHONE, now=now)
        outbound.record_accepted(OUTBOUND_ID, message_kind="template", now=now)
        outbound.record_receipt(DeliveryReceipt(
            OUTBOUND_ID,
            "delivered",
            now + timedelta(seconds=15),
        ), now=now)
        memory.record_consent(PHONE, "granted", "recovery-v1")
        mission = memory.create_mission(
            PHONE,
            title="Recovery mission",
            topic="document",
            next_step="Send reply",
            due_at="2026-08-10",
            metadata={"source": "whatsapp", "language": "ar", "category": "document"},
        )
        reminder = reminders.create(
            PHONE,
            title="Recovery reminder",
            scheduled_at=now + timedelta(days=2),
            language="ar",
            mission_id=str(mission["mission_id"]),
        )
        pending.put(
            PHONE,
            {
                "title": "Pending recovery document",
                "topic": "document",
                "next_step": "Review deadline",
                "due_at": "2026-08-10",
                "authority": "Jobcenter",
                "source_kind": "pdf",
            },
            now=now,
        )
        entitlements.set_plan(PHONE, "hero", source="recovery_drill")
        entitlements.check_and_consume(PHONE, "documents_monthly", amount=2, now=now)
        abuse.check(PHONE, now=now)
        provider.record("groq", "chat", "success", latency_ms=321, now=now)
        ticket = support.create(PHONE, language="ar", category="document", urgency="normal")
        feedback.record(5, language="ar", topic="document")
        return {
            "mission_id": str(mission["mission_id"]),
            "reminder_id": str(reminder["reminder_id"]),
            "ticket_id": str(ticket["ticket_id"]),
        }
    finally:
        store.close()


def test_encrypted_backup_restores_complete_application_state(tmp_path: Path) -> None:
    source_url = os.environ["RECOVERY_SOURCE_DATABASE_URL"]
    target_url = os.environ["RECOVERY_TARGET_DATABASE_URL"]
    admin_url = os.environ["RECOVERY_ADMIN_DATABASE_URL"]
    target_name = os.environ.get("RECOVERY_TARGET_DATABASE", "amthero24_restore")
    encryption_key = Fernet.generate_key().decode("ascii")

    identifiers = _seed_source(source_url)
    source_tables = _table_names(source_url)
    source_counts = _counts(source_url)
    assert EXPECTED_TABLES <= source_tables
    assert all(source_counts.get(table, 0) >= 1 for table in (
        "hero_users",
        "inbound_messages",
        "inbound_work_queue",
        "outbound_delivery_messages",
        "hero_missions",
        "hero_reminders",
        "pending_document_actions",
        "hero_entitlements",
        "hero_usage_counters",
        "abuse_rate_windows",
        "provider_operational_events",
        "human_support_tickets",
        "anonymous_feedback",
    ))

    artifact, manifest_path, manifest = create_backup(
        source_url,
        tmp_path,
        encryption_key=encryption_key,
        keep=2,
        now=datetime(2026, 7, 26, 13, tzinfo=UTC),
    )

    encrypted_bytes = artifact.read_bytes()
    assert manifest["encrypted"] is True
    assert manifest_path.exists()
    assert PHONE.encode("utf-8") not in encrypted_bytes
    assert OUTBOUND_ID.encode("utf-8") not in encrypted_bytes
    assert b"RecoveryTest" not in encrypted_bytes
    assert b"recovery test message" not in encrypted_bytes

    _reset_target(admin_url, target_name)
    restored = restore_backup(
        target_url,
        artifact,
        confirmation="RESTORE_AMTHERO24",
        restore_allowed=True,
        encryption_key=encryption_key,
        manifest_path=manifest_path,
    )
    assert restored["status"] == "restored"

    target_tables = _table_names(target_url)
    target_counts = _counts(target_url)
    assert source_tables == target_tables
    assert source_counts == target_counts

    target_store = PostgresDataStore(target_url)
    try:
        profile = target_store.get_user(PHONE)
        assert profile["first_name"] == "RecoveryTest"
        assert profile["preferred_language"] == "ar"
        assert target_store.recent_user_messages(PHONE) == ["recovery test message"]

        queue_item = DurableQueueRepository(target_store).claim(
            "wamid.recovery-drill",
            now=datetime(2026, 7, 26, 12, tzinfo=UTC),
        )
        assert queue_item is not None
        assert queue_item.sender == PHONE
        assert queue_item.inbound_status == "sent"

        outbound = OutboundDeliveryRepository(target_store).state(OUTBOUND_ID)
        assert outbound is not None
        assert outbound["status"] == "delivered"
        assert outbound["message_kind"] == "template"

        reminder = ReminderRepository(target_store).list(PHONE, active_only=False, limit=10)[0]
        assert reminder["reminder_id"] == identifiers["reminder_id"]
        assert decrypt_recipient(str(reminder["recipient_ciphertext"])) == PHONE

        mission = HeroMemory(target_store).get_latest_mission(PHONE)
        assert mission is not None
        assert mission["mission_id"] == identifiers["mission_id"]
        assert mission["next_step"] == "Send reply"

        ticket = SupportRepository(target_store).latest_for_user(PHONE)
        assert ticket is not None
        assert ticket["ticket_id"] == identifiers["ticket_id"]

        with target_store.pool.connection() as connection:
            raw_phone = connection.execute(
                "SELECT COUNT(*) AS count FROM hero_users WHERE phone_hash = %s",
                (PHONE,),
            ).fetchone()
            hashed_phone = connection.execute(
                "SELECT COUNT(*) AS count FROM hero_users WHERE phone_hash = %s",
                (PHONE_HASH,),
            ).fetchone()
        assert int(raw_phone["count"]) == 0
        assert int(hashed_phone["count"]) == 1
    finally:
        target_store.close()
