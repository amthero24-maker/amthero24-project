"""Persistent privacy-safe recovery evidence for outbound delivery health.

The state contains no message hash, recipient identifier, message content, or provider
error text. A failed delivery keeps launch readiness in warning until a strictly later
`delivered` or `read` receipt proves that outbound delivery recovered. The aggregate
state survives normal per-message receipt retention cleanup.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from outbound_delivery import DeliveryReceipt

_STATE_KEY = "global"
_SUCCESS_STATUSES = {"delivered", "read"}
_EVIDENCE_VALUES = {"none", "success_only", "success_after_failure", "unresolved_failure"}


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


def _safe_failure_code(value: Any) -> str:
    raw = str(value or "").strip()
    clean = "".join(character for character in raw if character.isalnum() or character in {"_", "-"})[:40]
    return clean or "unknown"


def _later(existing: Any, candidate: datetime) -> bool:
    parsed = _as_datetime(existing)
    return parsed is None or candidate > parsed


class OutboundDeliveryRecoveryState:
    """Store one aggregate failure/success ordering record for the deployment."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres()
        else:
            self._initialize_json()

    def _initialize_postgres(self) -> None:
        with self.store.pool.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_delivery_recovery_state (
                    state_key TEXT PRIMARY KEY,
                    last_failure_at TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    last_failure_code TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CHECK (state_key = 'global')
                )
                """
            )
            connection.execute(
                """
                WITH latest_failure AS (
                    SELECT failed_at, failure_code
                    FROM outbound_delivery_messages
                    WHERE status = 'failed' AND failed_at IS NOT NULL
                    ORDER BY failed_at DESC, updated_at DESC
                    LIMIT 1
                ),
                latest_success AS (
                    SELECT MAX(COALESCE(read_at, delivered_at)) AS success_at
                    FROM outbound_delivery_messages
                    WHERE status IN ('delivered', 'read')
                )
                INSERT INTO outbound_delivery_recovery_state
                    (state_key, last_failure_at, last_success_at,
                     last_failure_code, updated_at)
                SELECT
                    'global',
                    latest_failure.failed_at,
                    latest_success.success_at,
                    COALESCE(latest_failure.failure_code, ''),
                    NOW()
                FROM latest_success
                LEFT JOIN latest_failure ON TRUE
                WHERE latest_failure.failed_at IS NOT NULL
                   OR latest_success.success_at IS NOT NULL
                ON CONFLICT (state_key) DO NOTHING
                """
            )

    def _initialize_json(self) -> None:
        def update(data: dict[str, Any]) -> None:
            existing = data.get("outbound_delivery_recovery")
            if isinstance(existing, dict):
                return
            last_failure_at: datetime | None = None
            last_success_at: datetime | None = None
            last_failure_code = ""
            for record in data.get("outbound_delivery", {}).values():
                if not isinstance(record, dict):
                    continue
                status = str(record.get("status") or "")
                if status == "failed":
                    failed_at = _as_datetime(record.get("failed_at"))
                    if failed_at is not None and (last_failure_at is None or failed_at > last_failure_at):
                        last_failure_at = failed_at
                        last_failure_code = _safe_failure_code(record.get("failure_code"))
                elif status in _SUCCESS_STATUSES:
                    success_at = _as_datetime(record.get("read_at")) or _as_datetime(record.get("delivered_at"))
                    if success_at is not None and (last_success_at is None or success_at > last_success_at):
                        last_success_at = success_at
            data["outbound_delivery_recovery"] = {
                "last_failure_at": last_failure_at.isoformat() if last_failure_at else None,
                "last_success_at": last_success_at.isoformat() if last_success_at else None,
                "last_failure_code": last_failure_code,
                "updated_at": _now().isoformat(),
            }

        self.store._transaction(update)

    def record_receipt(self, receipt: DeliveryReceipt, *, resulting_status: str) -> None:
        """Record only effective terminal evidence for a known outbound message."""
        status = str(resulting_status or "")
        if status == "unknown":
            return
        occurred_at = _now(receipt.occurred_at)
        if receipt.status == "failed" and status == "failed":
            self._record_failure(occurred_at, receipt.failure_code)
        elif receipt.status in _SUCCESS_STATUSES and status in _SUCCESS_STATUSES:
            self._record_success(occurred_at)

    def _record_failure(self, occurred_at: datetime, failure_code: str) -> None:
        code = _safe_failure_code(failure_code)
        current = _now()
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO outbound_delivery_recovery_state
                        (state_key, last_failure_at, last_failure_code, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (state_key) DO UPDATE SET
                        last_failure_code = CASE
                            WHEN outbound_delivery_recovery_state.last_failure_at IS NULL
                              OR EXCLUDED.last_failure_at > outbound_delivery_recovery_state.last_failure_at
                            THEN EXCLUDED.last_failure_code
                            ELSE outbound_delivery_recovery_state.last_failure_code
                        END,
                        last_failure_at = CASE
                            WHEN outbound_delivery_recovery_state.last_failure_at IS NULL
                              OR EXCLUDED.last_failure_at > outbound_delivery_recovery_state.last_failure_at
                            THEN EXCLUDED.last_failure_at
                            ELSE outbound_delivery_recovery_state.last_failure_at
                        END,
                        updated_at = GREATEST(
                            outbound_delivery_recovery_state.updated_at,
                            EXCLUDED.updated_at
                        )
                    """,
                    (_STATE_KEY, occurred_at, code, current),
                )
            return

        def update(data: dict[str, Any]) -> None:
            state = data.setdefault("outbound_delivery_recovery", {})
            if _later(state.get("last_failure_at"), occurred_at):
                state["last_failure_at"] = occurred_at.isoformat()
                state["last_failure_code"] = code
            state["updated_at"] = current.isoformat()

        self.store._transaction(update)

    def _record_success(self, occurred_at: datetime) -> None:
        current = _now()
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO outbound_delivery_recovery_state
                        (state_key, last_success_at, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (state_key) DO UPDATE SET
                        last_success_at = CASE
                            WHEN outbound_delivery_recovery_state.last_success_at IS NULL
                              OR EXCLUDED.last_success_at > outbound_delivery_recovery_state.last_success_at
                            THEN EXCLUDED.last_success_at
                            ELSE outbound_delivery_recovery_state.last_success_at
                        END,
                        updated_at = GREATEST(
                            outbound_delivery_recovery_state.updated_at,
                            EXCLUDED.updated_at
                        )
                    """,
                    (_STATE_KEY, occurred_at, current),
                )
            return

        def update(data: dict[str, Any]) -> None:
            state = data.setdefault("outbound_delivery_recovery", {})
            if _later(state.get("last_success_at"), occurred_at):
                state["last_success_at"] = occurred_at.isoformat()
            state["updated_at"] = current.isoformat()

        self.store._transaction(update)

    def snapshot(self) -> dict[str, Any]:
        """Return only bounded aggregate evidence suitable for protected metrics."""
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT last_failure_at, last_success_at, last_failure_code
                    FROM outbound_delivery_recovery_state
                    WHERE state_key = %s
                    """,
                    (_STATE_KEY,),
                ).fetchone()
            state = dict(row) if row else {}
        else:
            raw = self.store.snapshot().get("outbound_delivery_recovery", {})
            state = raw if isinstance(raw, dict) else {}

        last_failure_at = _as_datetime(state.get("last_failure_at"))
        last_success_at = _as_datetime(state.get("last_success_at"))
        recovery_required = bool(
            last_failure_at is not None
            and (last_success_at is None or last_success_at <= last_failure_at)
        )
        if recovery_required:
            evidence = "unresolved_failure"
        elif last_failure_at is not None and last_success_at is not None:
            evidence = "success_after_failure"
        elif last_success_at is not None:
            evidence = "success_only"
        else:
            evidence = "none"
        if evidence not in _EVIDENCE_VALUES:
            evidence = "none"
        return {
            "recovery_required": recovery_required,
            "recovery_evidence": evidence,
            "recovery_failure_code": (
                _safe_failure_code(state.get("last_failure_code"))
                if recovery_required
                else ""
            ),
        }
