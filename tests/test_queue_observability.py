"""Aggregate durable-queue observability and launch-policy tests."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from admin_metrics import contains_personal_fields
from queue_launch_policy import augment_launch_report, queue_launch_check
from queue_observability import build_queue_overview


def _queue(**overrides):
    payload = {
        "mode": "configured",
        "total": 0,
        "by_status": {"queued": 0, "processing": 0, "completed": 0, "dead": 0},
        "ready": 0,
        "delayed": 0,
        "stale_processing": 0,
        "retrying": 0,
        "dead_24h": 0,
        "oldest_ready_age_seconds": 0,
        "max_attempt_count": 0,
    }
    payload.update(overrides)
    return payload


def test_non_postgres_overview_is_zeroed_and_contains_no_personal_fields(monkeypatch) -> None:
    monkeypatch.delenv("DURABLE_QUEUE_ENABLED", raising=False)
    overview = build_queue_overview(
        SimpleNamespace(backend_name="json"),
        now=datetime(2026, 8, 9, 9, 0, tzinfo=UTC),
    )

    assert overview == {
        "mode": "disabled",
        "total": 0,
        "by_status": {"queued": 0, "processing": 0, "completed": 0, "dead": 0},
        "ready": 0,
        "delayed": 0,
        "stale_processing": 0,
        "retrying": 0,
        "dead_24h": 0,
        "oldest_ready_age_seconds": 0,
        "max_attempt_count": 0,
    }
    assert contains_personal_fields({"durable_queue": overview}) is False


def test_launch_policy_warns_while_queue_is_intentionally_disabled() -> None:
    check = queue_launch_check({"durable_queue": _queue(mode="disabled")})
    assert check["status"] == "warning"
    assert check["code"] == "durable_queue"
    assert "MESSAGE_QUEUE_ENCRYPTION_KEY" in check["action"]


def test_launch_policy_is_ready_for_an_empty_healthy_queue() -> None:
    check = queue_launch_check({"durable_queue": _queue()})
    assert check == {
        "code": "durable_queue",
        "status": "ready",
        "detail": "Durable inbound queue has no material backlog, stale lease, or recent dead-letter signal.",
    }


def test_launch_policy_blocks_material_dead_letters_stale_leases_and_age() -> None:
    assert queue_launch_check({"durable_queue": _queue(dead_24h=5)})["status"] == "blocked"
    assert queue_launch_check({"durable_queue": _queue(stale_processing=5)})["status"] == "blocked"
    assert queue_launch_check({"durable_queue": _queue(oldest_ready_age_seconds=1800)})["status"] == "blocked"
    assert queue_launch_check({"durable_queue": _queue(mode="misconfigured")})["status"] == "blocked"


def test_launch_policy_warns_before_blocking_thresholds() -> None:
    check = queue_launch_check({
        "durable_queue": _queue(
            ready=100,
            stale_processing=1,
            dead_24h=1,
            oldest_ready_age_seconds=300,
        )
    })
    assert check["status"] == "warning"
    assert "dead-letter 24h: 1" in check["detail"]
    assert "stale leases: 1" in check["detail"]
    assert "ready backlog: 100" in check["detail"]


def test_augment_launch_report_is_idempotent_and_recomputes_summary() -> None:
    base = {
        "status": "ready",
        "summary": {"ready": 1, "warning": 0, "blocked": 0},
        "checks": [
            {"code": "postgresql", "status": "ready", "detail": "ok"},
            {"code": "durable_queue", "status": "ready", "detail": "old"},
        ],
        "next_actions": [],
    }
    overview = {"durable_queue": _queue(dead_24h=1)}

    once = augment_launch_report(base, overview)
    twice = augment_launch_report(once, overview)

    assert once == twice
    assert once["status"] == "warning"
    assert once["summary"] == {"ready": 1, "warning": 1, "blocked": 0}
    assert [item["code"] for item in once["checks"]].count("durable_queue") == 1
    assert len(once["next_actions"]) == 1
    encoded = json.dumps(once, ensure_ascii=False)
    for forbidden in ("phone", "sender", "message_id", "ciphertext", "raw_text"):
        assert forbidden not in encoded
