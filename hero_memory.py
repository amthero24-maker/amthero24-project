"""Consent-aware Hero Memory and Mission persistence for AmtHero24."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

_NEXT_STEP_PREFIX = "@mission-next-step:"
_LAST_ACTION_PREFIX = "@mission-last-action:"
_WAITING_PREFIX = "@mission-status:waiting"
_DUE_PREFIX = "@mission-due:"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _phone_hash(phone: str) -> str:
    normalized = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _directive(title: str) -> tuple[str, str] | None:
    value = str(title or "")
    if value.startswith(_NEXT_STEP_PREFIX):
        return "next_step", value[len(_NEXT_STEP_PREFIX):]
    if value.startswith(_LAST_ACTION_PREFIX):
        return "last_action", value[len(_LAST_ACTION_PREFIX):]
    if value == _WAITING_PREFIX:
        return "waiting", ""
    if value.startswith(_DUE_PREFIX):
        return "due", value[len(_DUE_PREFIX):]
    return None


class HeroMemory:
    """Structured memory layered over either PostgreSQL or atomic JSON."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _initialize_postgres_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS hero_missions (
                mission_id TEXT PRIMARY KEY,
                phone_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                last_action TEXT NOT NULL DEFAULT '',
                next_step TEXT NOT NULL DEFAULT '',
                due_at DATE,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            )
            """,
            "ALTER TABLE hero_missions ADD COLUMN IF NOT EXISTS last_action TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE hero_missions ADD COLUMN IF NOT EXISTS due_at DATE",
            """
            CREATE INDEX IF NOT EXISTS hero_missions_phone_status_idx
            ON hero_missions (phone_hash, status, updated_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS memory_consent_events (
                event_id BIGSERIAL PRIMARY KEY,
                phone_hash TEXT NOT NULL,
                decision TEXT NOT NULL,
                consent_version TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'whatsapp',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS memory_consent_phone_created_idx
            ON memory_consent_events (phone_hash, created_at DESC)
            """,
        )
        with self.store.pool.connection() as conn:
            for statement in statements:
                conn.execute(statement)

    def record_consent(self, phone: str, decision: str, version: str, *, source: str = "whatsapp") -> None:
        normalized_decision = decision if decision in {"granted", "declined", "withdrawn"} else "declined"
        event = {
            "phone_hash": _phone_hash(phone),
            "decision": normalized_decision,
            "consent_version": _clean_text(version, 80),
            "source": _clean_text(source, 40) or "whatsapp",
            "created_at": _now(),
        }
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO memory_consent_events
                        (phone_hash, decision, consent_version, source)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (event["phone_hash"], event["decision"], event["consent_version"], event["source"]),
                )
            return

        def add(data: dict[str, Any]) -> None:
            data.setdefault("audit_log", []).append(event)
            data["audit_log"] = data["audit_log"][-5000:]

        self.store._transaction(add)

    def create_mission(
        self,
        phone: str,
        *,
        title: str,
        topic: str = "",
        last_action: str = "",
        next_step: str = "",
        due_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        directive = _directive(title)
        if directive:
            action, value = directive
            if action == "next_step":
                return self.update_latest_mission(phone, next_step=value, operation="next_step")
            if action == "last_action":
                return self.update_latest_mission(phone, last_action=value, operation="last_action")
            if action == "waiting":
                return self.update_latest_mission(phone, status="waiting", operation="waiting")
            if action == "due":
                return self.update_latest_mission(phone, due_at=value, operation="due")

        mission = {
            "mission_id": uuid4().hex,
            "phone_hash": _phone_hash(phone),
            "title": _clean_text(title, 180) or "Open task",
            "topic": _clean_text(topic, 80),
            "status": "open",
            "last_action": _clean_text(last_action, 300),
            "next_step": _clean_text(next_step, 300),
            "due_at": due_at or None,
            "metadata": deepcopy(metadata or {}),
            "created_at": _now(),
            "updated_at": _now(),
            "completed_at": None,
            "_operation": "created",
        }
        allowed_metadata = {"source", "language", "category"}
        mission["metadata"] = {
            key: _clean_text(value, 80)
            for key, value in mission["metadata"].items()
            if key in allowed_metadata
        }

        if self.backend_name == "postgresql":
            with self.store.pool.connection() as conn:
                row = conn.execute(
                    """
                    INSERT INTO hero_missions
                        (mission_id, phone_hash, title, topic, status, last_action, next_step, due_at, metadata)
                    VALUES (%s, %s, %s, %s, 'open', %s, %s, %s, %s)
                    RETURNING mission_id, title, topic, status, last_action, next_step, due_at, metadata,
                              created_at, updated_at, completed_at
                    """,
                    (
                        mission["mission_id"], mission["phone_hash"], mission["title"], mission["topic"],
                        mission["last_action"], mission["next_step"], mission["due_at"], Jsonb(mission["metadata"]),
                    ),
                ).fetchone()
            result = self._mission_from_row(row)
            result["_operation"] = "created"
            return result

        def add(data: dict[str, Any]) -> dict[str, Any]:
            stored = {key: value for key, value in mission.items() if key != "_operation"}
            data.setdefault("cases", {})[mission["mission_id"]] = deepcopy(stored)
            return deepcopy(mission)

        return self.store._transaction(add)

    def list_missions(self, phone: str, *, status: str = "open", limit: int = 5) -> list[dict[str, Any]]:
        key = _phone_hash(phone)
        safe_limit = max(1, min(int(limit), 20))
        valid_status = status if status in {"open", "waiting", "completed", "all"} else "open"
        if self.backend_name == "postgresql":
            if valid_status == "all":
                condition = "phone_hash = %s"
                params: tuple[Any, ...] = (key,)
            elif valid_status == "open":
                condition = "phone_hash = %s AND status IN ('open', 'waiting')"
                params = (key,)
            else:
                condition = "phone_hash = %s AND status = %s"
                params = (key, valid_status)
            with self.store.pool.connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT mission_id, title, topic, status, last_action, next_step, due_at, metadata,
                           created_at, updated_at, completed_at
                    FROM hero_missions
                    WHERE {condition}
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (*params, safe_limit),
                ).fetchall()
            return [self._mission_from_row(row) for row in rows]

        snapshot = self.store.snapshot()
        missions = [
            deepcopy(record)
            for record in snapshot.get("cases", {}).values()
            if isinstance(record, dict)
            and record.get("phone_hash") == key
            and (
                valid_status == "all"
                or (valid_status == "open" and record.get("status") in {"open", "waiting"})
                or record.get("status") == valid_status
            )
        ]
        missions.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return missions[:safe_limit]

    def get_latest_mission(self, phone: str) -> dict[str, Any] | None:
        missions = self.list_missions(phone, status="open", limit=1)
        return missions[0] if missions else None

    def update_latest_mission(
        self,
        phone: str,
        *,
        last_action: str | None = None,
        next_step: str | None = None,
        due_at: str | None = None,
        status: str | None = None,
        operation: str = "updated",
    ) -> dict[str, Any]:
        key = _phone_hash(phone)
        clean_status = status if status in {"open", "waiting"} else None
        clean_last_action = _clean_text(last_action, 300) if last_action is not None else None
        clean_next_step = _clean_text(next_step, 300) if next_step is not None else None
        clean_due = due_at if due_at else None

        if self.backend_name == "postgresql":
            assignments: list[str] = []
            values: list[Any] = []
            if clean_last_action is not None:
                assignments.append("last_action = %s")
                values.append(clean_last_action)
            if clean_next_step is not None:
                assignments.append("next_step = %s")
                values.append(clean_next_step)
            if due_at is not None:
                assignments.append("due_at = %s")
                values.append(clean_due)
            if clean_status is not None:
                assignments.append("status = %s")
                values.append(clean_status)
            if not assignments:
                latest = self.get_latest_mission(phone)
                if latest:
                    latest["_operation"] = operation
                    return latest
                return {"_operation": "missing"}

            with self.store.pool.connection() as conn:
                row = conn.execute(
                    f"""
                    WITH latest AS (
                        SELECT mission_id
                        FROM hero_missions
                        WHERE phone_hash = %s AND status IN ('open', 'waiting')
                        ORDER BY updated_at DESC
                        LIMIT 1
                        FOR UPDATE
                    )
                    UPDATE hero_missions AS mission
                    SET {", ".join(assignments)}, updated_at = NOW()
                    FROM latest
                    WHERE mission.mission_id = latest.mission_id
                    RETURNING mission.mission_id, mission.title, mission.topic, mission.status,
                              mission.last_action, mission.next_step, mission.due_at, mission.metadata,
                              mission.created_at, mission.updated_at, mission.completed_at
                    """,
                    (key, *values),
                ).fetchone()
            if not row:
                return {"_operation": "missing"}
            result = self._mission_from_row(row)
            result["_operation"] = operation
            return result

        def update(data: dict[str, Any]) -> dict[str, Any]:
            candidates = [
                record
                for record in data.setdefault("cases", {}).values()
                if isinstance(record, dict)
                and record.get("phone_hash") == key
                and record.get("status") in {"open", "waiting"}
            ]
            if not candidates:
                return {"_operation": "missing"}
            candidates.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
            mission = candidates[0]
            if clean_last_action is not None:
                mission["last_action"] = clean_last_action
            if clean_next_step is not None:
                mission["next_step"] = clean_next_step
            if due_at is not None:
                mission["due_at"] = clean_due
            if clean_status is not None:
                mission["status"] = clean_status
            mission["updated_at"] = _now()
            result = deepcopy(mission)
            result["_operation"] = operation
            return result

        return self.store._transaction(update)

    def complete_latest_mission(self, phone: str) -> dict[str, Any] | None:
        key = _phone_hash(phone)
        completed_at = _now()
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as conn:
                row = conn.execute(
                    """
                    WITH latest AS (
                        SELECT mission_id
                        FROM hero_missions
                        WHERE phone_hash = %s AND status IN ('open', 'waiting')
                        ORDER BY updated_at DESC
                        LIMIT 1
                        FOR UPDATE
                    )
                    UPDATE hero_missions AS mission
                    SET status = 'completed', completed_at = NOW(), updated_at = NOW()
                    FROM latest
                    WHERE mission.mission_id = latest.mission_id
                    RETURNING mission.mission_id, mission.title, mission.topic, mission.status,
                              mission.last_action, mission.next_step, mission.due_at, mission.metadata,
                              mission.created_at, mission.updated_at, mission.completed_at
                    """,
                    (key,),
                ).fetchone()
            return self._mission_from_row(row) if row else None

        def complete(data: dict[str, Any]) -> dict[str, Any] | None:
            candidates = [
                record
                for record in data.setdefault("cases", {}).values()
                if isinstance(record, dict)
                and record.get("phone_hash") == key
                and record.get("status") in {"open", "waiting"}
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
            mission = candidates[0]
            mission["status"] = "completed"
            mission["completed_at"] = completed_at
            mission["updated_at"] = completed_at
            return deepcopy(mission)

        return self.store._transaction(complete)

    def export_user_data(self, phone: str) -> dict[str, Any]:
        profile = self.store.get_user(phone)
        safe_profile_fields = {
            "first_name", "city", "preferred_language", "current_topic", "communication_style",
            "memory_consent", "memory_consent_at", "memory_consent_version", "onboarding_stage",
        }
        profile_export = {
            key: profile[key]
            for key in safe_profile_fields
            if key in profile and profile[key] not in (None, "")
        }
        return {
            "profile": profile_export,
            "missions": self.list_missions(phone, status="all", limit=20),
            "exported_at": _now(),
        }

    def delete_all_user_data(self, phone: str) -> bool:
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as conn:
                conn.execute("DELETE FROM hero_missions WHERE phone_hash = %s", (key,))
                conn.execute(
                    """
                    INSERT INTO memory_consent_events
                        (phone_hash, decision, consent_version, source)
                    VALUES (%s, 'withdrawn', 'user-delete', 'whatsapp')
                    """,
                    (key,),
                )
            return self.store.delete_user(phone)

        def cleanup(data: dict[str, Any]) -> None:
            cases = data.setdefault("cases", {})
            for mission_id in [
                mission_id
                for mission_id, record in cases.items()
                if isinstance(record, dict) and record.get("phone_hash") == key
            ]:
                del cases[mission_id]

        self.store._transaction(cleanup)
        return self.store.delete_user(phone)

    @staticmethod
    def _mission_from_row(row: Any) -> dict[str, Any]:
        if not row:
            return {}
        result = dict(row)
        result["metadata"] = dict(result.get("metadata") or {})
        for field in ("created_at", "updated_at", "completed_at"):
            value = result.get(field)
            if isinstance(value, datetime):
                result[field] = value.astimezone(UTC).isoformat()
        due = result.get("due_at")
        if isinstance(due, (date, datetime)):
            result["due_at"] = due.date().isoformat() if isinstance(due, datetime) else due.isoformat()
        return result

    def export_as_json(self, phone: str) -> str:
        return json.dumps(self.export_user_data(phone), ensure_ascii=False, indent=2, sort_keys=True)
