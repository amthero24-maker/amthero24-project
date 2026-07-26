"""Consent-aware Hero Memory and Mission persistence for AmtHero24.

The existing data store owns user profiles and inbound-message deduplication. This
module adds structured missions and an immutable consent audit while preserving a
JSON fallback for local development and safe Railway deploys without PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _phone_hash(phone: str) -> str:
    normalized = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


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
                next_step TEXT NOT NULL DEFAULT '',
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            )
            """,
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
        next_step: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mission = {
            "mission_id": uuid4().hex,
            "phone_hash": _phone_hash(phone),
            "title": _clean_text(title, 180) or "Open task",
            "topic": _clean_text(topic, 80),
            "status": "open",
            "next_step": _clean_text(next_step, 300),
            "metadata": deepcopy(metadata or {}),
            "created_at": _now(),
            "updated_at": _now(),
            "completed_at": None,
        }
        # Never allow arbitrary sensitive payloads into structured mission metadata.
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
                        (mission_id, phone_hash, title, topic, status, next_step, metadata)
                    VALUES (%s, %s, %s, %s, 'open', %s, %s)
                    RETURNING mission_id, title, topic, status, next_step, metadata,
                              created_at, updated_at, completed_at
                    """,
                    (
                        mission["mission_id"], mission["phone_hash"], mission["title"],
                        mission["topic"], mission["next_step"], Jsonb(mission["metadata"]),
                    ),
                ).fetchone()
            return self._mission_from_row(row)

        def add(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault("cases", {})[mission["mission_id"]] = deepcopy(mission)
            return deepcopy(mission)

        return self.store._transaction(add)

    def list_missions(self, phone: str, *, status: str = "open", limit: int = 5) -> list[dict[str, Any]]:
        key = _phone_hash(phone)
        safe_limit = max(1, min(int(limit), 20))
        valid_status = status if status in {"open", "completed", "all"} else "open"
        if self.backend_name == "postgresql":
            condition = "phone_hash = %s" if valid_status == "all" else "phone_hash = %s AND status = %s"
            params: tuple[Any, ...] = (key,) if valid_status == "all" else (key, valid_status)
            with self.store.pool.connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT mission_id, title, topic, status, next_step, metadata,
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
            and (valid_status == "all" or record.get("status") == valid_status)
        ]
        missions.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return missions[:safe_limit]

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
                        WHERE phone_hash = %s AND status = 'open'
                        ORDER BY updated_at DESC
                        LIMIT 1
                        FOR UPDATE
                    )
                    UPDATE hero_missions AS mission
                    SET status = 'completed', completed_at = NOW(), updated_at = NOW()
                    FROM latest
                    WHERE mission.mission_id = latest.mission_id
                    RETURNING mission.mission_id, mission.title, mission.topic, mission.status,
                              mission.next_step, mission.metadata, mission.created_at,
                              mission.updated_at, mission.completed_at
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
                and record.get("status") == "open"
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
                # Keep a minimal withdrawal event for accountability; no profile data is retained.
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
        return result

    def export_as_json(self, phone: str) -> str:
        return json.dumps(self.export_user_data(phone), ensure_ascii=False, indent=2, sort_keys=True)
