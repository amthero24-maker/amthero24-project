"""Real PostgreSQL tests for hashed WhatsApp delivery receipts and aggregates."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

import runtime_health
import admin_extensions as admin_module
import launch_extensions as launch_module
from admin_metrics import contains_personal_fields
from outbound_delivery import DeliveryReceipt, OutboundDeliveryRepository
from outbound_delivery_recovery import OutboundDeliveryRecoveryState


@pytest.fixture(autouse=True)
def clean_delivery_rows(monkeypatch) -> None:
    monkeypatch.setenv("OUTBOUND_DELIVERY_RETENTION_DAYS", "30")
    store = runtime_health.store
    assert store.backend_name == "postgresql"
    OutboundDeliveryRepository(store)
    OutboundDeliveryRecoveryState(store)
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE outbound_delivery_recovery_state, outbound_delivery_messages")
    yield
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE outbound_delivery_recovery_state, outbound_delivery_messages")


def test_postgres_receipts_are_replica_safe_hashed_and_monotonic() -> None:
    store = runtime_health.store
    repository = OutboundDeliveryRepository(store)
    message_id = "wamid.pg-private-delivery-id"
    start = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    assert repository.record_accepted(message_id, message_kind="text", now=start)

    receipts = [
        DeliveryReceipt(message_id, "failed", start + timedelta(seconds=30), "131047"),
        DeliveryReceipt(message_id, "sent", start + timedelta(seconds=10)),
        DeliveryReceipt(message_id, "sent", start + timedelta(seconds=20)),
        DeliveryReceipt(message_id, "delivered", start + timedelta(seconds=40)),
        DeliveryReceipt(message_id, "delivered", start + timedelta(seconds=45)),
        DeliveryReceipt(message_id, "read", start + timedelta(seconds=50)),
        DeliveryReceipt(message_id, "failed", start + timedelta(seconds=60), "131000"),
    ]
    with ThreadPoolExecutor(max_workers=7) as executor:
        list(executor.map(repository.record_receipt, receipts))

    state = repository.state(message_id)
    assert state["status"] == "read"
    assert state["sent_at"] == (start + timedelta(seconds=10)).isoformat()
    assert state["delivered_at"] == (start + timedelta(seconds=40)).isoformat()
    assert state["read_at"] == (start + timedelta(seconds=50)).isoformat()
    assert state["failure_code"] in {"131047", "131000"}

    with store.pool.connection() as connection:
        row = connection.execute(
            "SELECT message_hash, message_kind, status FROM outbound_delivery_messages"
        ).fetchone()
    assert row["message_hash"] == hashlib.sha256(message_id.encode()).hexdigest()
    assert row["message_hash"] != message_id
    assert row["message_kind"] == "text"
    assert row["status"] == "read"
    assert message_id not in json.dumps(dict(row), ensure_ascii=False)


def test_postgres_recovery_bootstraps_and_survives_receipt_retention() -> None:
    store = runtime_health.store
    repository = OutboundDeliveryRepository(store)
    start = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    failed_id = "wamid.pg-recovery-failed"
    repository.record_accepted(failed_id, now=start)
    failure = DeliveryReceipt(failed_id, "failed", start + timedelta(minutes=1), "131031")
    assert repository.record_receipt(failure, now=start + timedelta(minutes=1)) == "failed"

    recovery = OutboundDeliveryRecoveryState(store)
    assert recovery.snapshot() == {
        "recovery_required": True,
        "recovery_evidence": "unresolved_failure",
        "recovery_failure_code": "131031",
    }

    with store.pool.connection() as connection:
        connection.execute(
            "UPDATE outbound_delivery_messages SET expires_at = %s",
            (start - timedelta(seconds=1),),
        )
    assert repository.cleanup(now=start) == 1
    assert recovery.snapshot()["recovery_required"] is True

    sent_id = "wamid.pg-recovery-sent"
    repository.record_accepted(sent_id, now=start + timedelta(minutes=2))
    sent = DeliveryReceipt(sent_id, "sent", start + timedelta(minutes=3))
    sent_state = repository.record_receipt(sent, now=start + timedelta(minutes=3))
    recovery.record_receipt(sent, resulting_status=sent_state)
    assert recovery.snapshot()["recovery_required"] is True

    delivered_id = "wamid.pg-recovery-delivered"
    repository.record_accepted(delivered_id, now=start + timedelta(minutes=4))
    delivered = DeliveryReceipt(delivered_id, "delivered", start + timedelta(minutes=5))
    delivered_state = repository.record_receipt(delivered, now=start + timedelta(minutes=5))
    recovery.record_receipt(delivered, resulting_status=delivered_state)
    assert recovery.snapshot() == {
        "recovery_required": False,
        "recovery_evidence": "success_after_failure",
        "recovery_failure_code": "",
    }

    with store.pool.connection() as connection:
        state_row = connection.execute(
            "SELECT state_key, last_failure_code FROM outbound_delivery_recovery_state"
        ).fetchone()
    encoded = json.dumps(dict(state_row), default=str, sort_keys=True)
    assert state_row["state_key"] == "global"
    assert state_row["last_failure_code"] == "131031"
    assert failed_id not in encoded
    assert sent_id not in encoded
    assert delivered_id not in encoded


def test_postgres_aggregate_admin_and_launch_reports_are_person_free() -> None:
    store = runtime_health.store
    repository = OutboundDeliveryRepository(store)
    current = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    records = {
        "accepted": ("wamid.pg-accepted", "+491708880001", "PRIVATE_ACCEPTED"),
        "sent": ("wamid.pg-sent", "+491708880002", "PRIVATE_SENT"),
        "delivered": ("wamid.pg-delivered", "+491708880003", "PRIVATE_DELIVERED"),
        "read": ("wamid.pg-read", "+491708880004", "PRIVATE_READ"),
        "failed": ("wamid.pg-failed", "+491708880005", "PRIVATE_FAILED"),
    }
    for offset, (status, (message_id, _phone, _marker)) in enumerate(records.items(), start=1):
        repository.record_accepted(
            message_id,
            message_kind="template" if status == "failed" else "text",
            now=current - timedelta(minutes=20 + offset),
        )
        if status != "accepted":
            repository.record_receipt(
                DeliveryReceipt(
                    message_id,
                    status,
                    current - timedelta(minutes=offset),
                    "131047" if status == "failed" else "",
                ),
                now=current,
            )

    overview = repository.aggregate(now=current)
    assert overview == {
        "tracked_24h": 5,
        "by_status": {"accepted": 1, "sent": 1, "delivered": 1, "read": 1, "failed": 1},
        "terminal_24h": 3,
        "delivery_success_pct": 66.7,
        "pending_over_15m": 2,
        "oldest_pending_age_seconds": 1320,
    }

    admin = admin_module.build_overview(store, now=current, version="test", model="test")
    assert admin["outbound_delivery"] == {
        **overview,
        "failure_codes": {"131047": 1},
        "recovery_required": False,
        "recovery_evidence": "success_after_failure",
        "recovery_failure_code": "",
    }
    assert contains_personal_fields(admin) is False

    launch = launch_module.build_launch_report(admin, environment={
        "DATABASE_URL": "postgresql://configured",
        "DATABASE_FALLBACK_ALLOWED": "false",
        "META_APP_SECRET": "configured-meta-secret-2026",
        "WEBHOOK_SIGNATURE_REQUIRED": "true",
        "ADMIN_API_TOKEN": "configured-admin-token-2026-unique",
        "REMINDER_WORKER_ENABLED": "false",
        "PRIVACY_RETENTION_ENABLED": "true",
        "PROVIDER_TELEMETRY_ENABLED": "true",
        "ABUSE_GUARD_ENABLED": "true",
        "ABUSE_GUARD_ENFORCEMENT_ENABLED": "true",
        "DURABLE_QUEUE_ENABLED": "true",
        "MESSAGE_QUEUE_ENCRYPTION_KEY": "configured-queue-key-2026-unique-value",
    })
    checks = {item["code"]: item for item in launch["checks"]}
    assert checks["outbound_delivery"]["status"] == "warning"

    encoded = json.dumps({"admin": admin, "launch": launch}, ensure_ascii=False, sort_keys=True)
    for message_id, phone, marker in records.values():
        assert message_id not in encoded
        assert phone not in encoded
        assert marker not in encoded


def test_postgres_cleanup_removes_only_expired_delivery_metadata() -> None:
    store = runtime_health.store
    repository = OutboundDeliveryRepository(store)
    current = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    expired = "wamid.pg-expired"
    current_id = "wamid.pg-current"
    repository.record_accepted(expired, now=current)
    repository.record_accepted(current_id, now=current)

    with store.pool.connection() as connection:
        connection.execute(
            "UPDATE outbound_delivery_messages SET expires_at = %s WHERE message_hash = %s",
            (current - timedelta(seconds=1), hashlib.sha256(expired.encode()).hexdigest()),
        )

    assert repository.cleanup(now=current) == 1
    assert repository.state(expired) is None
    assert repository.state(current_id)["status"] == "accepted"
