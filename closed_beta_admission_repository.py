"""Atomic, privacy-safe persistence for controlled Closed Beta admission.

The repository never sends invitations or enables admission. Callers must supply an
explicit :class:`AdmissionPolicy`; the default policy remains disabled.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from closed_beta_admission import AdmissionDecision, AdmissionPolicy

logger = logging.getLogger("amthero24.closed_beta_admission")
_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


@dataclass(frozen=True)
class AdmissionRepositoryStatus:
    enabled: bool
    capacity: int
    admitted_count: int
    remaining_slots: int
    full: bool
    verified: bool
    decision: AdmissionDecision
    changed: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def _phone_hash(phone: str) -> str:
    normalized = "".join(
        character
        for character in str(phone or "")
        if character.isdigit() or character == "+"
    )
    if not normalized:
        raise ValueError("beta_recipient_invalid")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _consent_version(value: str) -> str:
    clean = " ".join(str(value or "").split())[:80]
    if not clean:
        raise ValueError("beta_consent_version_required")
    return clean


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


class ClosedBetaAdmissionRepository:
    """Atomically claims capacity using hashed recipient identifiers only."""

    def __init__(
        self,
        store: Any,
        *,
        tenant_key: str = "default",
        wave: str = "wave1",
    ) -> None:
        if not _SCOPE_PATTERN.fullmatch(str(tenant_key or "")):
            raise ValueError("beta_tenant_key_invalid")
        if not _SCOPE_PATTERN.fullmatch(str(wave or "")):
            raise ValueError("beta_wave_invalid")
        self.store = store
        self.tenant_key = str(tenant_key)
        self.wave = str(wave)
        self.backend_name = str(getattr(store, "backend_name", "json"))
        self._schema_ready = self.backend_name != "postgresql"
        if self.backend_name == "postgresql":
            self._schema_ready = self._initialize_postgres_schema()

    @property
    def schema_ready(self) -> bool:
        return self._schema_ready

    def _initialize_postgres_schema(self) -> bool:
        try:
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS closed_beta_admissions (
                        tenant_key TEXT NOT NULL,
                        wave TEXT NOT NULL,
                        phone_hash TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
                        consent_version TEXT NOT NULL,
                        admitted_at TIMESTAMPTZ NOT NULL,
                        revoked_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (tenant_key, wave, phone_hash)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS closed_beta_admissions_scope_status_idx
                    ON closed_beta_admissions (tenant_key, wave, status, admitted_at)
                    """
                )
            return True
        except Exception as exc:  # pragma: no cover - exercised with a broken fake store
            logger.warning(
                "Closed Beta admission schema unavailable: %s",
                type(exc).__name__,
            )
            return False

    @staticmethod
    def _safe_capacity(policy: AdmissionPolicy) -> int:
        try:
            capacity = int(policy.capacity)
        except (TypeError, ValueError):
            return 0
        return capacity if 1 <= capacity <= 100 else 0

    @classmethod
    def _blocked(
        cls,
        policy: AdmissionPolicy,
        *,
        decision: AdmissionDecision = AdmissionDecision.BLOCKED,
    ) -> AdmissionRepositoryStatus:
        return AdmissionRepositoryStatus(
            enabled=bool(policy.enabled),
            capacity=cls._safe_capacity(policy),
            admitted_count=0,
            remaining_slots=0,
            full=True,
            verified=False,
            decision=decision,
        )

    @staticmethod
    def _result(
        policy: AdmissionPolicy,
        admitted_count: int,
        decision: AdmissionDecision,
        *,
        changed: bool = False,
    ) -> AdmissionRepositoryStatus:
        remaining = max(policy.capacity - admitted_count, 0)
        return AdmissionRepositoryStatus(
            enabled=policy.enabled,
            capacity=policy.capacity,
            admitted_count=admitted_count,
            remaining_slots=remaining,
            full=remaining == 0,
            verified=True,
            decision=decision,
            changed=changed,
        )

    def claim(
        self,
        phone: str,
        *,
        policy: AdmissionPolicy,
        beta_opt_in: bool,
        consent_version: str = "",
    ) -> AdmissionRepositoryStatus:
        """Claim one slot atomically; active-recipient replays are idempotent."""
        try:
            policy.validate()
        except (TypeError, ValueError):
            return self._blocked(policy)
        if not policy.enabled:
            return self._blocked(policy, decision=AdmissionDecision.DISABLED)
        if not beta_opt_in:
            return self._blocked(policy, decision=AdmissionDecision.NEEDS_OPT_IN)
        try:
            key = _phone_hash(phone)
            version = _consent_version(consent_version)
        except ValueError:
            return self._blocked(policy)
        if not self._schema_ready:
            return self._blocked(policy)
        try:
            if self.backend_name == "postgresql":
                return self._claim_postgres(key, policy, version)
            return self._claim_json(key, policy, version)
        except Exception as exc:
            logger.warning(
                "Closed Beta admission claim unavailable: %s",
                type(exc).__name__,
            )
            return self._blocked(policy)

    def _lock_material(self) -> str:
        # User deletion spans all waves, so every mutating operation uses one tenant-level
        # lock. This prevents an in-flight claim from recreating data during deletion.
        return f"amthero24:closed-beta:{self.tenant_key}"

    def _lock_postgres(self, connection: Any) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
            (self._lock_material(),),
        )

    def _claim_postgres(
        self,
        key: str,
        policy: AdmissionPolicy,
        consent_version: str,
    ) -> AdmissionRepositoryStatus:
        current = _now()
        with self.store.pool.connection() as connection:
            with connection.transaction():
                self._lock_postgres(connection)
                existing = connection.execute(
                    """
                    SELECT status
                    FROM closed_beta_admissions
                    WHERE tenant_key = %s AND wave = %s AND phone_hash = %s
                    FOR UPDATE
                    """,
                    (self.tenant_key, self.wave, key),
                ).fetchone()
                count_row = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM closed_beta_admissions
                    WHERE tenant_key = %s AND wave = %s AND status = 'active'
                    """,
                    (self.tenant_key, self.wave),
                ).fetchone()
                admitted_count = int(count_row["count"])
                if existing and str(existing["status"]) == "active":
                    return self._result(
                        policy,
                        admitted_count,
                        AdmissionDecision.ALREADY_ADMITTED,
                    )
                if admitted_count >= policy.capacity:
                    return self._result(
                        policy,
                        admitted_count,
                        AdmissionDecision.FULL,
                    )
                connection.execute(
                    """
                    INSERT INTO closed_beta_admissions
                        (tenant_key, wave, phone_hash, status, consent_version,
                         admitted_at, revoked_at)
                    VALUES (%s, %s, %s, 'active', %s, %s, NULL)
                    ON CONFLICT (tenant_key, wave, phone_hash) DO UPDATE
                    SET status = 'active',
                        consent_version = EXCLUDED.consent_version,
                        admitted_at = EXCLUDED.admitted_at,
                        revoked_at = NULL,
                        updated_at = NOW()
                    """,
                    (
                        self.tenant_key,
                        self.wave,
                        key,
                        consent_version,
                        current,
                    ),
                )
                return self._result(
                    policy,
                    admitted_count + 1,
                    AdmissionDecision.ADMITTED,
                    changed=True,
                )

    def _claim_json(
        self,
        key: str,
        policy: AdmissionPolicy,
        consent_version: str,
    ) -> AdmissionRepositoryStatus:
        current = _now().isoformat()

        def claim(data: dict[str, Any]) -> AdmissionRepositoryStatus:
            records = data.setdefault("closed_beta_admissions", {})
            tenant_records = records.setdefault(self.tenant_key, {})
            wave_records = tenant_records.setdefault(self.wave, {})
            existing = wave_records.get(key)
            admitted_count = sum(
                1
                for record in wave_records.values()
                if isinstance(record, dict) and record.get("status") == "active"
            )
            if isinstance(existing, dict) and existing.get("status") == "active":
                return self._result(
                    policy,
                    admitted_count,
                    AdmissionDecision.ALREADY_ADMITTED,
                )
            if admitted_count >= policy.capacity:
                return self._result(
                    policy,
                    admitted_count,
                    AdmissionDecision.FULL,
                )
            wave_records[key] = {
                "status": "active",
                "consent_version": consent_version,
                "admitted_at": current,
                "revoked_at": None,
                "updated_at": current,
            }
            return self._result(
                policy,
                admitted_count + 1,
                AdmissionDecision.ADMITTED,
                changed=True,
            )

        return self.store._transaction(claim)

    def status(self, policy: AdmissionPolicy) -> AdmissionRepositoryStatus:
        try:
            policy.validate()
        except (TypeError, ValueError):
            return self._blocked(policy)
        if not self._schema_ready:
            return self._blocked(policy)
        try:
            admitted_count = self._active_count()
        except Exception as exc:
            logger.warning(
                "Closed Beta admission status unavailable: %s",
                type(exc).__name__,
            )
            return self._blocked(policy)
        decision = (
            AdmissionDecision.DISABLED
            if not policy.enabled
            else (
                AdmissionDecision.FULL
                if admitted_count >= policy.capacity
                else AdmissionDecision.ADMITTED
            )
        )
        return self._result(policy, admitted_count, decision)

    def _active_count(self) -> int:
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM closed_beta_admissions
                    WHERE tenant_key = %s AND wave = %s AND status = 'active'
                    """,
                    (self.tenant_key, self.wave),
                ).fetchone()
            return int(row["count"])
        snapshot = self.store.snapshot()
        wave_records = (
            snapshot.get("closed_beta_admissions", {})
            .get(self.tenant_key, {})
            .get(self.wave, {})
        )
        return sum(
            1
            for record in wave_records.values()
            if isinstance(record, dict) and record.get("status") == "active"
        )

    def is_admitted(self, phone: str) -> bool:
        try:
            key = _phone_hash(phone)
            if not self._schema_ready:
                return False
            if self.backend_name == "postgresql":
                with self.store.pool.connection() as connection:
                    row = connection.execute(
                        """
                        SELECT 1
                        FROM closed_beta_admissions
                        WHERE tenant_key = %s AND wave = %s
                          AND phone_hash = %s AND status = 'active'
                        """,
                        (self.tenant_key, self.wave, key),
                    ).fetchone()
                return row is not None
            snapshot = self.store.snapshot()
            record = (
                snapshot.get("closed_beta_admissions", {})
                .get(self.tenant_key, {})
                .get(self.wave, {})
                .get(key)
            )
            return isinstance(record, dict) and record.get("status") == "active"
        except Exception:
            return False

    def export_user_status(self, phone: str) -> dict[str, Any]:
        """Return this tenant's admission metadata without recipient identifiers."""
        try:
            key = _phone_hash(phone)
            if not self._schema_ready:
                return {"status": "unavailable", "records": []}
            if self.backend_name == "postgresql":
                with self.store.pool.connection() as connection:
                    rows = connection.execute(
                        """
                        SELECT wave, status, consent_version, admitted_at, revoked_at
                        FROM closed_beta_admissions
                        WHERE tenant_key = %s AND phone_hash = %s
                        ORDER BY wave
                        """,
                        (self.tenant_key, key),
                    ).fetchall()
                records = [
                    {
                        "wave": str(row["wave"])[:40],
                        "status": str(row["status"])[:20],
                        "consent_version": str(row["consent_version"])[:80],
                        "admitted_at": _timestamp(row["admitted_at"]),
                        "revoked_at": _timestamp(row["revoked_at"]),
                    }
                    for row in rows
                ]
            else:
                snapshot = self.store.snapshot()
                tenant_records = snapshot.get("closed_beta_admissions", {}).get(
                    self.tenant_key,
                    {},
                )
                records = []
                for wave, wave_records in sorted(tenant_records.items()):
                    if not isinstance(wave_records, dict):
                        continue
                    record = wave_records.get(key)
                    if not isinstance(record, dict):
                        continue
                    records.append(
                        {
                            "wave": str(wave)[:40],
                            "status": str(record.get("status") or "")[:20],
                            "consent_version": str(
                                record.get("consent_version") or ""
                            )[:80],
                            "admitted_at": _timestamp(record.get("admitted_at")),
                            "revoked_at": _timestamp(record.get("revoked_at")),
                        }
                    )
            return {
                "status": "available",
                "active": any(record["status"] == "active" for record in records),
                "records": records,
            }
        except Exception as exc:
            logger.warning(
                "Closed Beta admission export unavailable: %s",
                type(exc).__name__,
            )
            return {"status": "unavailable", "records": []}

    def revoke(self, phone: str) -> bool:
        """Release one active slot. Missing or replayed revocations are harmless."""
        try:
            key = _phone_hash(phone)
            current = _now()
            if not self._schema_ready:
                return False
            if self.backend_name == "postgresql":
                with self.store.pool.connection() as connection:
                    with connection.transaction():
                        self._lock_postgres(connection)
                        cursor = connection.execute(
                            """
                            UPDATE closed_beta_admissions
                            SET status = 'revoked', revoked_at = %s, updated_at = NOW()
                            WHERE tenant_key = %s AND wave = %s
                              AND phone_hash = %s AND status = 'active'
                            """,
                            (current, self.tenant_key, self.wave, key),
                        )
                return cursor.rowcount == 1

            def revoke_record(data: dict[str, Any]) -> bool:
                record = (
                    data.setdefault("closed_beta_admissions", {})
                    .setdefault(self.tenant_key, {})
                    .setdefault(self.wave, {})
                    .get(key)
                )
                if not isinstance(record, dict) or record.get("status") != "active":
                    return False
                record["status"] = "revoked"
                record["revoked_at"] = current.isoformat()
                record["updated_at"] = current.isoformat()
                return True

            return bool(self.store._transaction(revoke_record))
        except Exception as exc:
            logger.warning(
                "Closed Beta admission revoke unavailable: %s",
                type(exc).__name__,
            )
            return False

    def delete_user(self, phone: str) -> bool:
        """Delete admission metadata across waves for this tenant only."""
        try:
            key = _phone_hash(phone)
            if not self._schema_ready:
                return False
            if self.backend_name == "postgresql":
                with self.store.pool.connection() as connection:
                    with connection.transaction():
                        self._lock_postgres(connection)
                        cursor = connection.execute(
                            """
                            DELETE FROM closed_beta_admissions
                            WHERE tenant_key = %s AND phone_hash = %s
                            """,
                            (self.tenant_key, key),
                        )
                return cursor.rowcount > 0

            def delete_record(data: dict[str, Any]) -> bool:
                tenant_records = data.setdefault(
                    "closed_beta_admissions",
                    {},
                ).setdefault(self.tenant_key, {})
                removed = False
                for wave_records in tenant_records.values():
                    if isinstance(wave_records, dict):
                        removed = wave_records.pop(key, None) is not None or removed
                return removed

            return bool(self.store._transaction(delete_record))
        except Exception as exc:
            logger.warning(
                "Closed Beta admission deletion unavailable: %s",
                type(exc).__name__,
            )
            return False
