"""Plan, quota, privacy, and aggregate entitlement tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore
from entitlement_engine import EntitlementRepository, plan_summary_message
from entitlement_metrics import build_entitlement_overview


def test_observe_only_records_usage_without_blocking(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENTITLEMENT_DEFAULT_PLAN", "beta")
    monkeypatch.setenv("ENTITLEMENT_BETA_IMAGES_MONTHLY", "1")
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT_ENABLED", "false")
    store = JsonDataStore(tmp_path / "store.json")
    repository = EntitlementRepository(store)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)

    first = repository.check_and_consume("49123", "images_monthly", now=now)
    second = repository.check_and_consume("49123", "images_monthly", now=now)

    assert first.allowed is True
    assert second.allowed is True
    assert second.used == 2
    assert second.reason == "observe_only"
    assert repository.summary("49123", now=now)["usage"]["images_monthly"] == 2


def test_enforcement_blocks_after_configured_limit_and_resets_by_period(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENTITLEMENT_FREE_DOCUMENTS_MONTHLY", "1")
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT_ENABLED", "true")
    store = JsonDataStore(tmp_path / "store.json")
    repository = EntitlementRepository(store)
    repository.set_plan("49123", "free")
    july = datetime(2026, 7, 26, 12, tzinfo=UTC)

    assert repository.check_and_consume("49123", "documents_monthly", now=july).allowed is True
    blocked = repository.check_and_consume("49123", "documents_monthly", now=july)
    assert blocked.allowed is False
    assert blocked.used == 1
    assert blocked.remaining == 0

    august = datetime(2026, 8, 1, 12, tzinfo=UTC)
    assert repository.check_and_consume("49123", "documents_monthly", now=august).allowed is True


def test_expired_assignment_falls_back_to_basic_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    repository = EntitlementRepository(store)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    repository.set_plan("49123", "hero", status="trial", valid_until=now - timedelta(seconds=1))

    assignment = repository.get_assignment("49123", now=now)

    assert assignment["plan_code"] == "free"
    assert assignment["status"] == "expired"


def test_export_and_deletion_contain_no_raw_phone(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    repository = EntitlementRepository(store)
    phone = "491234567"
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    repository.set_plan(phone, "hero", source="beta-grant")
    repository.check_and_consume(phone, "voice_monthly", now=now)

    exported = repository.export_user(phone, now=now)
    assert exported["plan"] == "hero"
    assert phone not in str(exported)
    assert repository.delete_user(phone) is True
    assert repository.summary(phone, now=now)["usage"]["voice_monthly"] == 0


def test_plan_message_and_admin_metrics_are_human_and_aggregate(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENTITLEMENT_DEFAULT_PLAN", "beta")
    store = JsonDataStore(tmp_path / "store.json")
    phone = "49123"
    store.update_user(phone, {"preferred_language": "ar"})
    repository = EntitlementRepository(store)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    repository.check_and_consume(phone, "images_monthly", now=now)

    summary = repository.summary(phone, now=now)
    message = plan_summary_message("ar", summary)
    overview = build_entitlement_overview(store, now=now)

    assert "خطتك الحالية" in message
    assert "الأسعار والدفع غير مفعّلين" in message
    assert overview["by_plan"] == {"beta": 1}
    assert overview["usage_current_period"]["images_monthly"] == 1
    assert phone not in str(overview)
