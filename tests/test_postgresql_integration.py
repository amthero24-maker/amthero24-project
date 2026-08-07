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
from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_execution_boundary import (
    BriefScannerExecutionCommandKind,
    BriefScannerMissionCommand,
    BriefScannerReminderCommand,
)
from brief_scanner_mission_planner import BriefScannerMissionKind
from brief_scanner_reminder_planner import BriefScannerReminderKind
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeBatch,
    BriefScannerRuntimeInvocation,
    brief_scanner_runtime_idempotency_key,
)
from brief_scanner_runtime_reminder_executor import (
    BriefScannerMissionReminderRuntimeExecutor,
    BriefScannerMissionReminderStatus,
)
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
    store.claim_message(
        "admin-overview-text-category",
        "+4915700000000",
        "private integration content",
        message_type="text",
    )
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
    assert overview.json()["messages_24h"]["by_type"] == {"text": 1}
    assert "private integration content" not in overview.text
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


def test_cross_replica_mission_and_reminder_batch_is_atomic() -> None:
    database_url = os.environ["DATABASE_URL"]
    first_store = PostgresDataStore(database_url)
    second_store = PostgresDataStore(database_url)
    phone = "+49159" + uuid4().hex[:8]
    fingerprint = hashlib.sha256(uuid4().bytes).hexdigest()
    mission_action = BriefScannerConsentAction.CREATE_MISSION
    reminder_action = BriefScannerConsentAction.CREATE_REMINDER
    batch = BriefScannerRuntimeBatch(
        planning_fingerprint=fingerprint,
        invocations=(
            BriefScannerRuntimeInvocation(
                action=mission_action,
                idempotency_key=brief_scanner_runtime_idempotency_key(
                    fingerprint,
                    mission_action,
                ),
                command=BriefScannerMissionCommand(
                    kind=BriefScannerExecutionCommandKind.CREATE_MISSION,
                    mission_kind=BriefScannerMissionKind.TRACK_DEADLINE,
                    title="Track document deadline",
                    topic="document",
                    next_step="Complete the required action before the deadline",
                    due_date=datetime(2026, 9, 1).date(),
                ),
            ),
            BriefScannerRuntimeInvocation(
                action=reminder_action,
                idempotency_key=brief_scanner_runtime_idempotency_key(
                    fingerprint,
                    reminder_action,
                ),
                command=BriefScannerReminderCommand(
                    kind=BriefScannerExecutionCommandKind.CREATE_REMINDER,
                    reminder_kind=BriefScannerReminderKind.DEADLINE,
                    title="Synthetic Authority",
                    target_date=datetime(2026, 9, 1).date(),
                    lead_days=3,
                    scheduled_at_utc=datetime(2026, 8, 29, 7, tzinfo=UTC),
                    timezone_name="Europe/Berlin",
                    local_delivery_time=datetime(2026, 8, 29, 9).time(),
                    source_language="de",
                    reference_number="SYNTHETIC-REF-POSTGRES",
                ),
            ),
        ),
    )

    try:
        executors = (
            BriefScannerMissionReminderRuntimeExecutor(
                first_store,
                phone=phone,
            ),
            BriefScannerMissionReminderRuntimeExecutor(
                second_store,
                phone=phone,
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda executor: executor(batch), executors))

        assert sorted(result.status for result in results) == [
            BriefScannerMissionReminderStatus.CREATED,
            BriefScannerMissionReminderStatus.REPLAYED,
        ]
        assert len({result.mission_id for result in results}) == 1
        assert len({result.reminder_id for result in results}) == 1
        with first_store.pool.connection() as connection:
            mission_count = connection.execute(
                "SELECT COUNT(*) AS count FROM hero_missions WHERE phone_hash = %s",
                (_phone_hash(phone),),
            ).fetchone()
            reminder_count = connection.execute(
                "SELECT COUNT(*) AS count FROM hero_reminders WHERE phone_hash = %s",
                (_phone_hash(phone),),
            ).fetchone()
        assert int(mission_count["count"]) == 1
        assert int(reminder_count["count"]) == 1
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


def test_postgres_recurring_reminder_advances_atomically() -> None:
    store = _store()
    phone = "+49161" + uuid4().hex[:8]
    repository = ReminderRepository(store)
    first = datetime.now(UTC) + timedelta(hours=1)
    reminder = repository.create(
        phone,
        title="Recurring integration reminder",
        scheduled_at=first,
        language="de",
        recurrence_days=7,
        recurrence_count=3,
    )

    repository.mark_sent(reminder["reminder_id"], now=first)
    active = repository.list(phone)
    assert len(active) == 1
    assert active[0]["status"] == "pending"
    assert active[0]["recurrence_days"] == 7
    assert active[0]["recurrence_remaining"] == 2
    assert datetime.fromisoformat(active[0]["scheduled_at"]) > first


def test_postgres_reminder_acknowledgement_is_atomic_and_recipient_scoped() -> None:
    store = _store()
    phone = "+49165" + uuid4().hex[:8]
    other_phone = "+49166" + uuid4().hex[:8]
    repository = ReminderRepository(store)
    delivered_at = datetime.now(UTC)
    reminder = repository.create(
        phone,
        title="Acknowledgement integration reminder",
        scheduled_at=delivered_at,
        language="de",
    )
    repository.mark_sent(reminder["reminder_id"], now=delivered_at)

    status, acknowledged = repository.acknowledge_recent(
        phone, now=delivered_at + timedelta(minutes=1),
    )

    assert status == "acknowledged"
    assert acknowledged["status"] == "acknowledged"
    assert acknowledged["acknowledged_sent_at"] == delivered_at.isoformat()
    assert repository.acknowledge_recent(
        phone, now=delivered_at + timedelta(minutes=2),
    )[0] == "already"
    assert repository.acknowledge_recent(
        other_phone, now=delivered_at + timedelta(minutes=2),
    )[0] == "not_found"
    with store.pool.connection() as connection:
        row = connection.execute(
            """
            SELECT status, acknowledged_at, acknowledged_sent_at
            FROM hero_reminders WHERE reminder_id = %s
            """,
            (reminder["reminder_id"],),
        ).fetchone()
    assert row["status"] == "acknowledged"
    assert row["acknowledged_at"] is not None
    assert row["acknowledged_sent_at"] == delivered_at


def test_postgres_snooze_is_atomic_and_preserves_recurring_source() -> None:
    store = _store()
    phone = "+49167" + uuid4().hex[:8]
    other_phone = "+49168" + uuid4().hex[:8]
    repository = ReminderRepository(store)
    delivered_at = datetime.now(UTC)
    source = repository.create(
        phone,
        title="Snooze integration reminder",
        scheduled_at=delivered_at,
        language="de",
        recurrence_days=1,
        recurrence_count=3,
    )
    repository.mark_sent(source["reminder_id"], now=delivered_at)
    source_before = next(
        item
        for item in repository.list(phone, active_only=False, limit=10)
        if item["reminder_id"] == source["reminder_id"]
    )
    target = delivered_at + timedelta(minutes=15)

    status, snooze = repository.snooze_recent(
        phone,
        scheduled_at=target,
        now=delivered_at + timedelta(minutes=1),
    )

    assert status == "snoozed"
    assert snooze["snooze_origin_id"] == source["reminder_id"]
    assert snooze["snooze_count"] == 1
    assert snooze["recurrence_days"] is None
    assert snooze["snooze_preserved_recurrence"] is True
    assert repository.snooze_recent(
        phone,
        scheduled_at=target,
        now=delivered_at + timedelta(minutes=2),
    )[0] == "existing"
    assert repository.snooze_recent(
        other_phone,
        scheduled_at=target,
        now=delivered_at + timedelta(minutes=1),
    )[0] == "not_found"
    with store.pool.connection() as connection:
        source_row = connection.execute(
            """
            SELECT status, scheduled_at, recurrence_remaining
            FROM hero_reminders WHERE reminder_id = %s
            """,
            (source["reminder_id"],),
        ).fetchone()
        snooze_row = connection.execute(
            """
            SELECT status, scheduled_at, recurrence_days, recurrence_remaining,
                   snooze_origin_id, snooze_count
            FROM hero_reminders WHERE reminder_id = %s
            """,
            (snooze["reminder_id"],),
        ).fetchone()
    assert source_row["status"] == "pending"
    assert source_row["scheduled_at"].isoformat() == source_before["scheduled_at"]
    assert source_row["recurrence_remaining"] == source_before["recurrence_remaining"]
    assert snooze_row["status"] == "pending"
    assert snooze_row["scheduled_at"] == target
    assert snooze_row["recurrence_days"] is None
    assert snooze_row["recurrence_remaining"] is None
    assert snooze_row["snooze_origin_id"] == source["reminder_id"]
    assert snooze_row["snooze_count"] == 1


def test_postgres_weekday_reminder_skips_weekend_atomically() -> None:
    store = _store()
    phone = "+49162" + uuid4().hex[:8]
    repository = ReminderRepository(store)
    friday = datetime(2026, 10, 23, 6, tzinfo=UTC)
    reminder = repository.create(
        phone,
        title="Weekday integration reminder",
        scheduled_at=friday,
        language="de",
        recurrence_days=1,
        recurrence_count=3,
        weekdays_only=True,
    )

    repository.mark_sent(reminder["reminder_id"], now=friday)

    active = repository.list(phone)
    assert len(active) == 1
    assert active[0]["weekdays_only"] is True
    assert active[0]["recurrence_remaining"] == 2
    assert active[0]["scheduled_at"] == datetime(2026, 10, 26, 7, tzinfo=UTC).isoformat()


def test_postgres_specific_weekdays_advance_atomically() -> None:
    store = _store()
    phone = "+49163" + uuid4().hex[:8]
    repository = ReminderRepository(store)
    thursday = datetime(2026, 10, 22, 6, tzinfo=UTC)
    reminder = repository.create(
        phone,
        title="Specific weekday integration reminder",
        scheduled_at=thursday,
        language="de",
        recurrence_days=1,
        recurrence_count=4,
        recurrence_weekdays=(0, 3),
    )

    repository.mark_sent(reminder["reminder_id"], now=thursday)

    active = repository.list(phone)
    assert len(active) == 1
    assert active[0]["recurrence_weekdays"] == "0,3"
    assert active[0]["recurrence_remaining"] == 3
    assert active[0]["scheduled_at"] == datetime(2026, 10, 26, 7, tzinfo=UTC).isoformat()


def test_postgres_statewide_holiday_schedule_advances_atomically() -> None:
    store = _store()
    phone = "+49164" + uuid4().hex[:8]
    repository = ReminderRepository(store)
    easter_monday = datetime(2026, 4, 6, 6, tzinfo=UTC)
    reminder = repository.create(
        phone,
        title="Holiday-aware integration reminder",
        scheduled_at=easter_monday,
        language="de",
        recurrence_days=1,
        recurrence_count=4,
        recurrence_weekdays=(0,),
        holiday_region="BE",
    )

    assert reminder["holiday_region"] == "BE"
    assert reminder["scheduled_at"] == datetime(2026, 4, 13, 6, tzinfo=UTC).isoformat()
    repository.mark_sent(reminder["reminder_id"], now=datetime(2026, 4, 13, 6, tzinfo=UTC))

    active = repository.list(phone)
    assert len(active) == 1
    assert active[0]["holiday_region"] == "BE"
    assert active[0]["recurrence_remaining"] == 3
    assert active[0]["scheduled_at"] == datetime(2026, 4, 20, 6, tzinfo=UTC).isoformat()


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
