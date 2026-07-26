"""Anonymous provider telemetry and durable Groq circuit protection."""
from __future__ import annotations

import os
import statistics
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


class ProviderCircuitOpen(RuntimeError):
    """Raised when a provider is temporarily isolated after repeated failures."""


@dataclass(frozen=True)
class CircuitDecision:
    allowed: bool
    provider: str
    blocked_until: datetime | None = None
    consecutive_failures: int = 0


def _flag(name: str, default: bool = True) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().casefold() in {"1", "true", "yes", "on"}


def telemetry_enabled() -> bool:
    return _flag("PROVIDER_TELEMETRY_ENABLED", True)


def circuit_enabled() -> bool:
    return _flag("GROQ_CIRCUIT_BREAKER_ENABLED", True)


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _clean(value: Any, limit: int) -> str:
    return "".join(character for character in str(value or "") if character.isalnum() or character in {"_", "-", "."})[:limit]


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return int(ordered[position])


class ProviderReliabilityRepository:
    """Stores aggregate provider events only; prompts, replies, and recipients never enter this layer."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _initialize_postgres_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS provider_operational_events (
                event_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                operation TEXT NOT NULL,
                outcome TEXT NOT NULL,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS provider_circuit_state (
                provider TEXT PRIMARY KEY,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                blocked_until TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS provider_events_created_idx ON provider_operational_events (created_at)",
            "CREATE INDEX IF NOT EXISTS provider_events_provider_outcome_idx ON provider_operational_events (provider, outcome, created_at)",
        )
        with self.store.pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    def before_call(self, provider: str, *, now: datetime | None = None) -> CircuitDecision:
        current = _now(now)
        clean_provider = _clean(provider, 40) or "unknown"
        if clean_provider != "groq" or not circuit_enabled():
            return CircuitDecision(True, clean_provider)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    "SELECT consecutive_failures, blocked_until FROM provider_circuit_state WHERE provider = %s",
                    (clean_provider,),
                ).fetchone()
            if not row:
                return CircuitDecision(True, clean_provider)
            blocked_until = _as_datetime(row.get("blocked_until"))
            if blocked_until and blocked_until > current:
                return CircuitDecision(False, clean_provider, blocked_until, int(row["consecutive_failures"]))
            return CircuitDecision(True, clean_provider, blocked_until, int(row["consecutive_failures"]))

        snapshot = self.store.snapshot()
        record = snapshot.get("provider_circuits", {}).get(clean_provider, {})
        if not isinstance(record, dict):
            return CircuitDecision(True, clean_provider)
        blocked_until = _as_datetime(record.get("blocked_until"))
        failures = int(record.get("consecutive_failures", 0))
        return CircuitDecision(not blocked_until or blocked_until <= current, clean_provider, blocked_until, failures)

    def record(
        self,
        provider: str,
        operation: str,
        outcome: str,
        latency_ms: int,
        *,
        error_code: str = "",
        now: datetime | None = None,
    ) -> None:
        if not telemetry_enabled():
            return
        current = _now(now)
        clean_provider = _clean(provider, 40) or "unknown"
        clean_operation = _clean(operation, 60) or "unknown"
        clean_outcome = _clean(outcome, 30) or "unknown"
        clean_error = _clean(error_code, 80)
        latency = max(0, min(int(latency_ms), 300_000))
        event = {
            "event_id": uuid4().hex,
            "provider": clean_provider,
            "operation": clean_operation,
            "outcome": clean_outcome,
            "latency_ms": latency,
            "error_code": clean_error,
            "created_at": current.isoformat(),
        }
        threshold = _int_env("GROQ_CIRCUIT_FAILURE_THRESHOLD", 5, 2, 50)
        cooldown = timedelta(seconds=_int_env("GROQ_CIRCUIT_COOLDOWN_SECONDS", 120, 15, 3600))

        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO provider_operational_events
                        (event_id, provider, operation, outcome, latency_ms, error_code, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (event["event_id"], clean_provider, clean_operation, clean_outcome, latency, clean_error, current),
                )
                if clean_provider == "groq" and circuit_enabled():
                    if clean_outcome == "success":
                        connection.execute(
                            """
                            INSERT INTO provider_circuit_state (provider, consecutive_failures, blocked_until)
                            VALUES ('groq', 0, NULL)
                            ON CONFLICT (provider) DO UPDATE
                            SET consecutive_failures = 0, blocked_until = NULL, updated_at = NOW()
                            """
                        )
                    elif clean_outcome == "failure":
                        row = connection.execute(
                            """
                            INSERT INTO provider_circuit_state (provider, consecutive_failures, blocked_until)
                            VALUES ('groq', 1, NULL)
                            ON CONFLICT (provider) DO UPDATE
                            SET consecutive_failures = provider_circuit_state.consecutive_failures + 1,
                                updated_at = NOW()
                            RETURNING consecutive_failures
                            """
                        ).fetchone()
                        failures = int(row["consecutive_failures"])
                        if failures >= threshold:
                            connection.execute(
                                "UPDATE provider_circuit_state SET blocked_until = %s, updated_at = NOW() WHERE provider = 'groq'",
                                (current + cooldown,),
                            )
            return

        def save(data: dict[str, Any]) -> None:
            events = data.setdefault("provider_events", [])
            events.append(event)
            data["provider_events"] = events[-20000:]
            if clean_provider != "groq" or not circuit_enabled():
                return
            circuits = data.setdefault("provider_circuits", {})
            state = circuits.setdefault("groq", {"consecutive_failures": 0, "blocked_until": None})
            if clean_outcome == "success":
                state["consecutive_failures"] = 0
                state["blocked_until"] = None
            elif clean_outcome == "failure":
                failures = int(state.get("consecutive_failures", 0)) + 1
                state["consecutive_failures"] = failures
                if failures >= threshold:
                    state["blocked_until"] = (current + cooldown).isoformat()
            state["updated_at"] = current.isoformat()

        self.store._transaction(save)

    def circuit_status(self, provider: str = "groq", *, now: datetime | None = None) -> str:
        return "closed" if self.before_call(provider, now=now).allowed else "open"

    def cleanup(self, *, now: datetime | None = None, retention_days: int = 30) -> int:
        current = _now(now)
        cutoff = current - timedelta(days=max(1, retention_days))
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                cursor = connection.execute("DELETE FROM provider_operational_events WHERE created_at < %s", (cutoff,))
            return max(cursor.rowcount, 0)

        def clean(data: dict[str, Any]) -> int:
            events = data.setdefault("provider_events", [])
            kept = [
                event for event in events
                if isinstance(event, dict) and (_as_datetime(event.get("created_at")) or current) >= cutoff
            ]
            removed = len(events) - len(kept)
            data["provider_events"] = kept
            return removed

        return int(self.store._transaction(clean))

    def aggregate(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        cutoff = current - timedelta(hours=24)
        records: list[dict[str, Any]] = []
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT provider, operation, outcome, latency_ms, error_code
                    FROM provider_operational_events WHERE created_at >= %s
                    """,
                    (cutoff,),
                ).fetchall()
            records = [dict(row) for row in rows]
        else:
            records = [
                deepcopy(event) for event in self.store.snapshot().get("provider_events", [])
                if isinstance(event, dict) and (_as_datetime(event.get("created_at")) or datetime.min.replace(tzinfo=UTC)) >= cutoff
            ]

        providers: dict[str, dict[str, Any]] = {}
        for record in records:
            provider = str(record.get("provider") or "unknown")
            bucket = providers.setdefault(provider, {
                "total": 0, "success": 0, "failure": 0, "circuit_rejected": 0,
                "latencies": [], "errors": {},
            })
            bucket["total"] += 1
            outcome = str(record.get("outcome") or "unknown")
            if outcome in {"success", "failure", "circuit_rejected"}:
                bucket[outcome] += 1
            bucket["latencies"].append(int(record.get("latency_ms", 0)))
            error = str(record.get("error_code") or "")
            if error:
                bucket["errors"][error] = int(bucket["errors"].get(error, 0)) + 1

        result: dict[str, Any] = {}
        for provider, bucket in sorted(providers.items()):
            latencies = list(bucket.pop("latencies"))
            bucket["latency_ms"] = {
                "average": int(statistics.mean(latencies)) if latencies else 0,
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "max": max(latencies) if latencies else 0,
            }
            bucket["errors"] = dict(sorted(bucket["errors"].items()))
            if provider == "groq":
                bucket["circuit"] = self.circuit_status("groq", now=current)
            result[provider] = bucket
        if "groq" not in result:
            result["groq"] = {
                "total": 0, "success": 0, "failure": 0, "circuit_rejected": 0,
                "errors": {}, "latency_ms": {"average": 0, "p50": 0, "p95": 0, "max": 0},
                "circuit": self.circuit_status("groq", now=current),
            }
        return result


def elapsed_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))
