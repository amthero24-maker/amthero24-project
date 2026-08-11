from datetime import UTC, datetime, timedelta

from outbound_delivery_observability import build_outbound_delivery_overview
from outbound_delivery_policy import outbound_delivery_check
from scripts.production_monitor import _outbound_delivery_detail


class SnapshotStore:
    backend_name = "json"

    def __init__(self, records):
        self._records = records

    def snapshot(self):
        return {"outbound_delivery": self._records}


def _record(now, *, status, code="", age_minutes=1):
    accepted = now - timedelta(minutes=age_minutes)
    return {
        "status": status,
        "accepted_at": accepted.isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "failure_code": code,
    }


def test_failure_codes_are_aggregate_bounded_and_current_only():
    now = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)
    store = SnapshotStore({
        "a": _record(now, status="failed", code="131047"),
        "b": _record(now, status="failed", code="131047"),
        "c": _record(now, status="failed", code="131026"),
        "d": _record(now, status="read"),
        "old": {
            "status": "failed",
            "accepted_at": (now - timedelta(hours=25)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "failure_code": "999999",
        },
    })

    report = build_outbound_delivery_overview(store, now=now)

    assert report["by_status"]["failed"] == 3
    assert report["by_status"]["read"] == 1
    assert report["failure_codes"] == {"131026": 1, "131047": 2}
    assert report["terminal_24h"] == 4
    assert report["delivery_success_pct"] == 25.0
    assert report["recovery_required"] is False
    assert report["recovery_evidence"] == "none"


def test_failure_code_output_never_reflects_provider_error_text_or_identifiers():
    now = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)
    store = SnapshotStore({
        "private-message-hash": _record(
            now,
            status="failed",
            code="131047<script>alert-private</script>",
        ),
    })

    report = build_outbound_delivery_overview(
        store,
        now=now,
        recovery={
            "recovery_required": True,
            "recovery_evidence": "unresolved_failure",
            "recovery_failure_code": "131031<script>private</script>",
        },
    )
    encoded = str(report)

    assert "script" in next(iter(report["failure_codes"]))
    assert report["recovery_failure_code"] == "131031scriptprivatescript"
    assert "<" not in encoded
    assert ">" not in encoded
    assert "private-message-hash" not in encoded


def test_blank_failure_code_is_grouped_as_unknown():
    now = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)
    report = build_outbound_delivery_overview(
        SnapshotStore({"a": _record(now, status="failed", code="")}),
        now=now,
    )
    assert report["failure_codes"] == {"unknown": 1}


def test_empty_24h_window_stays_warning_until_later_success_evidence():
    now = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
    report = build_outbound_delivery_overview(
        SnapshotStore({}),
        now=now,
        recovery={
            "recovery_required": True,
            "recovery_evidence": "unresolved_failure",
            "recovery_failure_code": "131031",
        },
    )
    check = outbound_delivery_check({"outbound_delivery": report})

    assert report["tracked_24h"] == 0
    assert report["recovery_required"] is True
    assert check["status"] == "warning"
    assert "131031" in check["detail"]


def test_production_monitor_prints_only_bounded_aggregate_failure_codes_and_recovery():
    detail = _outbound_delivery_detail({
        "outbound_delivery": {
            "tracked_24h": 2,
            "by_status": {"read": 1, "failed": 1},
            "failure_codes": {
                "131047": 1,
                "<private-text>": 999,
            },
            "terminal_24h": 2,
            "delivery_success_pct": 50,
            "pending_over_15m": 0,
            "oldest_pending_age_seconds": 0,
            "recovery_required": True,
            "recovery_evidence": "unresolved_failure",
            "recovery_failure_code": "131031",
        }
    })

    assert "failure_codes=private-text:999,131047:1" not in detail
    assert "131047:1" in detail
    assert "recovery_required=true" in detail
    assert "recovery_evidence=unresolved_failure" in detail
    assert "recovery_failure_code=131031" in detail
    assert "<" not in detail
    assert ">" not in detail
    assert "terminal_24h=2" in detail
