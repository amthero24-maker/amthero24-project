"""Aggregate entitlement metrics that never expose user identifiers."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from entitlement_engine import default_plan_code, enforcement_enabled


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _json_metrics(store: Any, current: datetime) -> dict[str, Any]:
    snapshot = store.snapshot()
    users = snapshot.get("users", {}) if isinstance(snapshot.get("users"), dict) else {}
    entitlements = snapshot.get("entitlements", {}) if isinstance(snapshot.get("entitlements"), dict) else {}
    counters = snapshot.get("usage_counters", {}) if isinstance(snapshot.get("usage_counters"), dict) else {}
    plans: Counter[str] = Counter()
    fallback = default_plan_code()
    for phone_hash in users:
        record = entitlements.get(phone_hash, {}) if isinstance(entitlements.get(phone_hash), dict) else {}
        plans[str(record.get("plan_code") or fallback)] += 1

    usage: Counter[str] = Counter()
    today = current.date().isoformat()
    month_start = current.date().replace(day=1).isoformat()
    for record in counters.values():
        if not isinstance(record, dict):
            continue
        metric = str(record.get("metric") or "unknown")
        period = str(record.get("period_start") or "")
        expected = today if metric.endswith("_daily") else month_start
        if period == expected:
            usage[metric] += int(record.get("count", 0))
    return {
        "by_plan": dict(sorted(plans.items())),
        "usage_current_period": dict(sorted(usage.items())),
        "mode": "enforced" if enforcement_enabled() else "observe-only",
    }


def _postgres_metrics(store: Any, current: datetime) -> dict[str, Any]:
    fallback = default_plan_code()
    with store.pool.connection() as connection:
        entitlement_table = connection.execute("SELECT to_regclass('hero_entitlements') AS name").fetchone()
        usage_table = connection.execute("SELECT to_regclass('hero_usage_counters') AS name").fetchone()
        if entitlement_table and entitlement_table.get("name"):
            rows = connection.execute(
                """
                WITH resolved_entitlements AS (
                    SELECT COALESCE(entitlement.plan_code, %s) AS plan_code
                    FROM hero_users AS hero
                    LEFT JOIN hero_entitlements AS entitlement
                      ON entitlement.phone_hash = hero.phone_hash
                )
                SELECT plan_code, COUNT(*) AS count
                FROM resolved_entitlements
                GROUP BY plan_code
                """,
                (fallback,),
            ).fetchall()
            plans = {str(row["plan_code"]): int(row["count"]) for row in rows}
        else:
            row = connection.execute("SELECT COUNT(*) AS count FROM hero_users").fetchone()
            plans = {fallback: int(row["count"] if row else 0)}

        usage: dict[str, int] = {}
        if usage_table and usage_table.get("name"):
            rows = connection.execute(
                """
                SELECT metric, SUM(count) AS count
                FROM hero_usage_counters
                WHERE (metric LIKE '%%_daily' AND period_start = %s)
                   OR (metric LIKE '%%_monthly' AND period_start = %s)
                GROUP BY metric
                """,
                (current.date(), current.date().replace(day=1)),
            ).fetchall()
            usage = {str(row["metric"]): int(row["count"]) for row in rows}
    return {
        "by_plan": dict(sorted(plans.items())),
        "usage_current_period": dict(sorted(usage.items())),
        "mode": "enforced" if enforcement_enabled() else "observe-only",
    }


def build_entitlement_overview(store: Any, *, now: datetime | None = None) -> dict[str, Any]:
    current = _now(now)
    if str(getattr(store, "backend_name", "json")) == "postgresql":
        return _postgres_metrics(store, current)
    return _json_metrics(store, current)
