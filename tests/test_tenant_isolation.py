"""Cross-user privacy boundary tests for the local atomic store.

These tests use only synthetic phone numbers and marker strings. They prove that every
user-scoped repository reads and mutates records through the requesting tenant key.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from abuse_guard import AbuseGuardRepository
from data_store import JsonDataStore
from document_action_repository import PendingDocumentRepository
from entitlement_engine import EntitlementRepository
from hero_memory import HeroMemory
from privacy_engine import export_user_data
from reminder_engine import ReminderRepository
from support_handoff import SupportRepository


def _repositories(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "tenant-isolation-reminder-key-2026-safe")
    monkeypatch.setenv("SUPPORT_ENCRYPTION_KEY", "tenant-isolation-support-key-2026-safe")
    monkeypatch.setenv("ABUSE_GUARD_ENABLED", "true")
    monkeypatch.setenv("ABUSE_GUARD_ENFORCEMENT_ENABLED", "false")
    store = JsonDataStore(tmp_path / "tenant-isolation.json")
    return (
        store,
        HeroMemory(store),
        ReminderRepository(store),
        PendingDocumentRepository(store),
        EntitlementRepository(store),
        AbuseGuardRepository(store),
        SupportRepository(store),
    )


def test_every_json_repository_is_scoped_to_the_requesting_user(tmp_path, monkeypatch) -> None:
    store, memory, reminders, pending, entitlements, abuse, support = _repositories(tmp_path, monkeypatch)
    alpha = "+491701110001"
    beta = "+491701110002"
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)

    store.update_user(alpha, {"first_name": "ALPHA_NAME", "preferred_language": "ar"})
    store.update_user(beta, {"first_name": "BETA_NAME", "preferred_language": "de"})
    store.claim_message("wamid.alpha", alpha, "ALPHA_MESSAGE")
    store.claim_message("wamid.beta", beta, "BETA_MESSAGE")

    alpha_mission = memory.create_mission(
        alpha,
        title="ALPHA_MISSION",
        topic="alpha-topic",
        next_step="ALPHA_NEXT",
    )
    beta_mission = memory.create_mission(
        beta,
        title="BETA_MISSION",
        topic="beta-topic",
        next_step="BETA_NEXT",
    )
    reminders.create(
        alpha,
        title="ALPHA_REMINDER",
        scheduled_at=now + timedelta(days=1),
        language="ar",
        mission_id=str(alpha_mission["mission_id"]),
    )
    reminders.create(
        beta,
        title="BETA_REMINDER",
        scheduled_at=now + timedelta(days=2),
        language="de",
        mission_id=str(beta_mission["mission_id"]),
    )
    pending.put(alpha, {"title": "ALPHA_DOCUMENT", "topic": "document", "next_step": "ALPHA_REVIEW"}, now=now)
    pending.put(beta, {"title": "BETA_DOCUMENT", "topic": "document", "next_step": "BETA_REVIEW"}, now=now)
    entitlements.set_plan(alpha, "hero", source="alpha-test")
    entitlements.set_plan(beta, "family", source="beta-test")
    alpha_usage = entitlements.check_and_consume(alpha, "documents_monthly", now=now)
    beta_usage = entitlements.check_and_consume(beta, "documents_monthly", amount=2, now=now)
    alpha_abuse = abuse.check(alpha, now=now)
    beta_abuse = abuse.check(beta, now=now)
    support.create(alpha, language="ar", category="document", urgency="normal")
    support.create(beta, language="de", category="account", urgency="high")

    assert store.get_user(alpha)["first_name"] == "ALPHA_NAME"
    assert store.get_user(beta)["first_name"] == "BETA_NAME"
    assert store.recent_user_messages(alpha) == ["ALPHA_MESSAGE"]
    assert store.recent_user_messages(beta) == ["BETA_MESSAGE"]
    assert [item["title"] for item in memory.list_missions(alpha, status="all")] == ["ALPHA_MISSION"]
    assert [item["title"] for item in memory.list_missions(beta, status="all")] == ["BETA_MISSION"]
    assert [item["title"] for item in reminders.list(alpha, active_only=False)] == ["ALPHA_REMINDER"]
    assert [item["title"] for item in reminders.list(beta, active_only=False)] == ["BETA_REMINDER"]
    assert pending.get(alpha, now=now)["title"] == "ALPHA_DOCUMENT"
    assert pending.get(beta, now=now)["title"] == "BETA_DOCUMENT"
    assert entitlements.get_assignment(alpha, now=now)["plan_code"] == "hero"
    assert entitlements.get_assignment(beta, now=now)["plan_code"] == "family"
    assert alpha_usage.used == 1
    assert beta_usage.used == 2
    assert alpha_abuse.minute_count == 1
    assert beta_abuse.minute_count == 1
    assert support.latest_for_user(alpha)["category"] == "document"
    assert support.latest_for_user(beta)["category"] == "account"


def test_user_actions_cannot_mutate_another_users_records(tmp_path, monkeypatch) -> None:
    _, memory, reminders, pending, _, _, support = _repositories(tmp_path, monkeypatch)
    alpha = "+491701120001"
    beta = "+491701120002"
    now = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)

    memory.create_mission(alpha, title="ALPHA_MISSION")
    memory.create_mission(beta, title="BETA_MISSION")
    reminders.create(alpha, title="ALPHA_REMINDER", scheduled_at=now + timedelta(days=1), language="ar")
    reminders.create(beta, title="BETA_REMINDER", scheduled_at=now + timedelta(days=1), language="de")
    pending.put(alpha, {"title": "ALPHA_DOCUMENT"}, now=now)
    pending.put(beta, {"title": "BETA_DOCUMENT"}, now=now)
    support.create(alpha, language="ar", category="general", urgency="normal")
    support.create(beta, language="de", category="technical", urgency="normal")

    assert memory.complete_latest_mission(alpha)["title"] == "ALPHA_MISSION"
    assert reminders.cancel(alpha, all_active=True) == 1
    assert pending.delete(alpha) is True
    assert support.cancel_latest(alpha)["status"] == "cancelled"

    assert memory.list_missions(beta, status="open")[0]["title"] == "BETA_MISSION"
    assert reminders.list(beta)[0]["status"] == "pending"
    assert pending.get(beta, now=now)["title"] == "BETA_DOCUMENT"
    assert support.latest_for_user(beta)["status"] == "open"


def test_privacy_export_contains_only_the_requesting_users_markers(tmp_path, monkeypatch) -> None:
    _, memory, reminders, _, _, _, _ = _repositories(tmp_path, monkeypatch)
    alpha = "+491701130001"
    beta = "+491701130002"
    now = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)

    memory.store.update_user(alpha, {"first_name": "ALPHA_EXPORT", "memory_consent": "granted"})
    memory.store.update_user(beta, {"first_name": "BETA_EXPORT", "memory_consent": "granted"})
    memory.create_mission(alpha, title="ALPHA_EXPORT_MISSION")
    memory.create_mission(beta, title="BETA_EXPORT_MISSION")
    reminders.create(alpha, title="ALPHA_EXPORT_REMINDER", scheduled_at=now + timedelta(days=1), language="ar")
    reminders.create(beta, title="BETA_EXPORT_REMINDER", scheduled_at=now + timedelta(days=1), language="de")

    payload = export_user_data(memory.store, alpha, memory.export_user_data(alpha), reminders)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert "ALPHA_EXPORT" in encoded
    assert "ALPHA_EXPORT_MISSION" in encoded
    assert "ALPHA_EXPORT_REMINDER" in encoded
    assert "BETA_EXPORT" not in encoded
    assert "BETA_EXPORT_MISSION" not in encoded
    assert "BETA_EXPORT_REMINDER" not in encoded
    assert beta not in encoded
