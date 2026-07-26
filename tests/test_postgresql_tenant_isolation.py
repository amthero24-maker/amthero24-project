"""Real PostgreSQL cross-tenant isolation and deletion-survival tests.

Runs only in the isolated PostgreSQL CI job. No production service or real user data is
used. The final production composition is imported before repositories are created so
all privacy deletion wrappers are exercised exactly as deployed.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import runtime_health
from abuse_guard import AbuseGuardRepository
from document_action_repository import PendingDocumentRepository
from durable_queue import DurableQueueRepository
from entitlement_engine import EntitlementRepository
from hero_memory import HeroMemory
from reminder_engine import ReminderRepository
from support_handoff import SupportRepository

_LINKED_TABLES = (
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
_SEEDED_TABLES = tuple(table for table in _LINKED_TABLES if table != "abuse_blocks")
_CLEANUP_TABLES = _LINKED_TABLES + (
    "inbound_work_queue",
    "abuse_guard_events",
    "provider_operational_events",
    "provider_circuit_state",
    "human_support_admin_events",
    "anonymous_feedback",
    "privacy_deletion_events",
)


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in phone if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def clean_tenant_records(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_QUEUE_ENCRYPTION_KEY", "queue-tenant-ci-2026-unique-7rT4mQ9xLp2V")
    store = runtime_health.store
    assert store.backend_name == "postgresql"
    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
        present = {str(row["tablename"]) for row in rows}
        tables = [table for table in _CLEANUP_TABLES if table in present]
        if tables:
            connection.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY")
    yield
    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
        present = {str(row["tablename"]) for row in rows}
        tables = [table for table in _CLEANUP_TABLES if table in present]
        if tables:
            connection.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY")


def _seed_user(phone: str, marker: str, *, language: str, plan: str, now: datetime) -> dict[str, object]:
    store = runtime_health.store
    memory = HeroMemory(store)
    reminders = ReminderRepository(store)
    pending = PendingDocumentRepository(store)
    entitlements = EntitlementRepository(store)
    abuse = AbuseGuardRepository(store)
    support = SupportRepository(store)
    durable_queue = DurableQueueRepository(store)

    store.update_user(phone, {
        "first_name": f"{marker}_NAME",
        "preferred_language": language,
        "memory_consent": "granted",
        "onboarding_stage": "complete",
    })
    message_id = f"wamid.{marker.lower()}.{uuid4().hex}"
    store.claim_message(message_id, phone, f"{marker}_MESSAGE")
    durable_queue.enqueue(message_id, phone, now=now)
    memory.record_consent(phone, "granted", "tenant-isolation-v1")
    mission = memory.create_mission(
        phone,
        title=f"{marker}_MISSION",
        topic=f"{marker.lower()}-topic",
        next_step=f"{marker}_NEXT",
    )
    reminders.create(
        phone,
        title=f"{marker}_REMINDER",
        scheduled_at=now + timedelta(days=1),
        language=language,
        mission_id=str(mission["mission_id"]),
    )
    pending.put(
        phone,
        {"title": f"{marker}_DOCUMENT", "topic": "document", "next_step": f"{marker}_REVIEW"},
        now=now,
    )
    entitlements.set_plan(phone, plan, source="tenant-isolation")
    entitlements.check_and_consume(phone, "documents_monthly", now=now)
    abuse.check(phone, now=now)
    support.create(phone, language=language, category=f"{marker.lower()}-category", urgency="normal")
    return {
        "memory": memory,
        "reminders": reminders,
        "pending": pending,
        "entitlements": entitlements,
        "support": support,
        "message_id": message_id,
    }


def test_postgres_reads_updates_and_cancellations_stay_inside_tenant_boundary() -> None:
    alpha = "+491702210001"
    beta = "+491702210002"
    now = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    alpha_repos = _seed_user(alpha, "ALPHA", language="ar", plan="hero", now=now)
    beta_repos = _seed_user(beta, "BETA", language="de", plan="family", now=now)

    memory = alpha_repos["memory"]
    reminders = alpha_repos["reminders"]
    pending = alpha_repos["pending"]
    support = alpha_repos["support"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        alpha_future = executor.submit(memory.update_latest_mission, alpha, next_step="ALPHA_UPDATED")
        beta_future = executor.submit(memory.update_latest_mission, beta, next_step="BETA_UPDATED")
        alpha_cancel = executor.submit(reminders.cancel, alpha, all_active=True)
        beta_read = executor.submit(reminders.list, beta)

    assert alpha_future.result()["title"] == "ALPHA_MISSION"
    assert beta_future.result()["title"] == "BETA_MISSION"
    assert alpha_cancel.result() == 1
    assert beta_read.result()[0]["title"] == "BETA_REMINDER"
    assert beta_read.result()[0]["status"] == "pending"

    assert pending.delete(alpha) is True
    assert support.cancel_latest(alpha)["status"] == "cancelled"

    assert memory.list_missions(alpha, status="all")[0]["next_step"] == "ALPHA_UPDATED"
    assert memory.list_missions(beta, status="all")[0]["next_step"] == "BETA_UPDATED"
    assert pending.get(beta, now=now)["title"] == "BETA_DOCUMENT"
    assert support.latest_for_user(beta)["status"] == "open"
    assert alpha_repos["entitlements"].get_assignment(alpha, now=now)["plan_code"] == "hero"
    assert beta_repos["entitlements"].get_assignment(beta, now=now)["plan_code"] == "family"

    alpha_export = json.dumps(memory.export_user_data(alpha), ensure_ascii=False, sort_keys=True)
    beta_export = json.dumps(memory.export_user_data(beta), ensure_ascii=False, sort_keys=True)
    assert "ALPHA_MISSION" in alpha_export and "BETA_MISSION" not in alpha_export
    assert "BETA_MISSION" in beta_export and "ALPHA_MISSION" not in beta_export


def test_deleting_one_tenant_preserves_every_other_tenant_layer() -> None:
    store = runtime_health.store
    alpha = "+491702220001"
    beta = "+491702220002"
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    alpha_repos = _seed_user(alpha, "ALPHA_DELETE", language="ar", plan="hero", now=now)
    beta_repos = _seed_user(beta, "BETA_KEEP", language="de", plan="family", now=now)
    alpha_key = _phone_hash(alpha)
    beta_key = _phone_hash(beta)

    assert alpha_repos["memory"].delete_all_user_data(alpha) is True

    with store.pool.connection() as connection:
        for table in _LINKED_TABLES:
            alpha_count = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE phone_hash = %s",
                (alpha_key,),
            ).fetchone()
            assert int(alpha_count["count"]) == 0, f"alpha remained in {table}"

        for table in _SEEDED_TABLES:
            beta_count = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE phone_hash = %s",
                (beta_key,),
            ).fetchone()
            assert int(beta_count["count"]) > 0, f"beta was removed from {table}"

        alpha_queue = connection.execute(
            "SELECT COUNT(*) AS count FROM inbound_work_queue WHERE message_id = %s",
            (alpha_repos["message_id"],),
        ).fetchone()
        beta_queue = connection.execute(
            "SELECT sender_ciphertext FROM inbound_work_queue WHERE message_id = %s",
            (beta_repos["message_id"],),
        ).fetchone()
        events = connection.execute(
            "SELECT COUNT(*) AS count FROM privacy_deletion_events"
        ).fetchone()
        raw_phone_occurrences = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM hero_users WHERE profile::text LIKE %s)
              + (SELECT COUNT(*) FROM inbound_messages WHERE text LIKE %s) AS count
            """,
            (f"%{beta}%", f"%{beta}%"),
        ).fetchone()

    assert int(alpha_queue["count"]) == 0
    assert beta_queue is not None
    assert beta not in str(beta_queue["sender_ciphertext"])
    assert int(events["count"]) == 1
    assert int(raw_phone_occurrences["count"]) == 0
    assert store.get_user(beta)["first_name"] == "BETA_KEEP_NAME"
    assert store.recent_user_messages(beta) == ["BETA_KEEP_MESSAGE"]
    assert beta_repos["memory"].list_missions(beta, status="all")[0]["title"] == "BETA_KEEP_MISSION"
    assert beta_repos["reminders"].list(beta, active_only=False)[0]["title"] == "BETA_KEEP_REMINDER"
    assert beta_repos["pending"].get(beta, now=now)["title"] == "BETA_KEEP_DOCUMENT"
    assert beta_repos["entitlements"].get_assignment(beta, now=now)["plan_code"] == "family"
    assert beta_repos["support"].latest_for_user(beta)["category"] == "beta_keep-category"
