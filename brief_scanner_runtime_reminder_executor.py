"""Atomic Mission + Reminder executor for authorized Brief Scanner batches.

The executor persists both records in one storage transaction or neither. It does not generate
drafts, start reminder workers, send WhatsApp messages, emit telemetry, or wire itself into the
application. The existing runtime and per-action gates remain the only dispatch entry point.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.types.json import Jsonb

from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_execution_boundary import (
    BriefScannerExecutionCommandKind,
    BriefScannerMissionCommand,
    BriefScannerReminderCommand,
)
from brief_scanner_mission_planner import BriefScannerMissionKind
from brief_scanner_reminder_planner import BriefScannerReminderKind
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeBatch,
    BriefScannerRuntimeInvocation,
    brief_scanner_runtime_idempotency_key,
)
from reminder_engine import SUPPORTED_LANGUAGES, encrypt_recipient


class BriefScannerReminderRuntimeError(RuntimeError):
    """Fail-closed composite executor error with a non-sensitive code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BriefScannerMissionReminderStatus(StrEnum):
    CREATED = "created"
    REPLAYED = "replayed"


@dataclass(frozen=True)
class BriefScannerMissionReminderResult:
    status: BriefScannerMissionReminderStatus
    planning_fingerprint: str
    mission_id: str
    reminder_id: str
    executed_actions: tuple[BriefScannerConsentAction, ...]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _phone_hash(phone: str) -> str:
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, tenant_hash: str, idempotency_key: str) -> str:
    material = f"{prefix}:{tenant_hash}:{idempotency_key}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _valid_digest(value: str) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and value == value.casefold()
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_phone(phone: str) -> str:
    if type(phone) is not str:
        raise BriefScannerReminderRuntimeError(
            "brief_scanner_reminder_runtime_tenant_invalid"
        )
    normalized = "".join(
        character for character in phone if character.isdigit() or character == "+"
    )
    digits = normalized.lstrip("+")
    if (
        not digits
        or not digits.isdigit()
        or normalized.count("+") > 1
        or ("+" in normalized and not normalized.startswith("+"))
        or len(normalized) > 32
    ):
        raise BriefScannerReminderRuntimeError(
            "brief_scanner_reminder_runtime_tenant_invalid"
        )
    return normalized


def _require_invocation(
    invocation: BriefScannerRuntimeInvocation,
    *,
    action: BriefScannerConsentAction,
    fingerprint: str,
) -> None:
    if (
        type(invocation) is not BriefScannerRuntimeInvocation
        or invocation.action is not action
        or invocation.authorized is not True
        or invocation.executed is not False
        or invocation.idempotency_key
        != brief_scanner_runtime_idempotency_key(fingerprint, action)
    ):
        raise BriefScannerReminderRuntimeError(
            "brief_scanner_reminder_runtime_invocation_invalid"
        )


def _require_batch(
    batch: BriefScannerRuntimeBatch,
) -> tuple[
    BriefScannerRuntimeInvocation,
    BriefScannerMissionCommand,
    BriefScannerRuntimeInvocation,
    BriefScannerReminderCommand,
]:
    if (
        type(batch) is not BriefScannerRuntimeBatch
        or not _valid_digest(batch.planning_fingerprint)
        or batch.requires_atomic_execution is not True
        or batch.allows_implicit_actions is not False
        or len(batch.invocations) != 2
    ):
        raise BriefScannerReminderRuntimeError(
            "brief_scanner_reminder_runtime_batch_invalid"
        )
    mission_invocation, reminder_invocation = batch.invocations
    _require_invocation(
        mission_invocation,
        action=BriefScannerConsentAction.CREATE_MISSION,
        fingerprint=batch.planning_fingerprint,
    )
    _require_invocation(
        reminder_invocation,
        action=BriefScannerConsentAction.CREATE_REMINDER,
        fingerprint=batch.planning_fingerprint,
    )
    mission = mission_invocation.command
    reminder = reminder_invocation.command
    mission_kinds = {
        BriefScannerMissionKind.PREPARE_RESPONSE,
        BriefScannerMissionKind.TRACK_DEADLINE,
        BriefScannerMissionKind.TRACK_APPOINTMENT,
    }
    if (
        type(mission) is not BriefScannerMissionCommand
        or mission.kind is not BriefScannerExecutionCommandKind.CREATE_MISSION
        or mission.authorized is not True
        or mission.executed is not False
        or mission.source != "brief_scanner"
        or type(mission.mission_kind) is not BriefScannerMissionKind
        or mission.mission_kind not in mission_kinds
        or type(reminder) is not BriefScannerReminderCommand
        or reminder.kind is not BriefScannerExecutionCommandKind.CREATE_REMINDER
        or type(reminder.reminder_kind) is not BriefScannerReminderKind
        or reminder.authorized is not True
        or reminder.executed is not False
        or type(reminder.source_language) is not str
        or reminder.source_language not in SUPPORTED_LANGUAGES
        or type(reminder.lead_days) is not int
        or not 0 <= reminder.lead_days <= 365
        or type(reminder.target_date) is not date
        or type(reminder.scheduled_at_utc) is not datetime
        or reminder.scheduled_at_utc.tzinfo is None
        or type(reminder.local_delivery_time) is not time
        or reminder.local_delivery_time.tzinfo is not None
        or reminder.local_delivery_time.second
        or reminder.local_delivery_time.microsecond
        or mission.due_date != reminder.target_date
    ):
        raise BriefScannerReminderRuntimeError(
            "brief_scanner_reminder_runtime_command_invalid"
        )
    text_fields = (
        (mission.title, 180),
        (mission.topic, 80),
        (mission.next_step, 300),
        (reminder.title, 180),
        (reminder.timezone_name, 80),
        (reminder.source_language, 16),
        (reminder.reference_number, 120),
    )
    if any(
        type(value) is not str
        or " ".join(value.split()).strip() != value
        or len(value) > limit
        for value, limit in text_fields
    ) or not mission.title or not reminder.title:
        raise BriefScannerReminderRuntimeError(
            "brief_scanner_reminder_runtime_command_invalid"
        )
    try:
        timezone = ZoneInfo(reminder.timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise BriefScannerReminderRuntimeError(
            "brief_scanner_reminder_runtime_schedule_invalid"
        ) from exc
    scheduled = reminder.scheduled_at_utc.astimezone(UTC)
    local = scheduled.astimezone(timezone)
    expected_date = date.fromordinal(
        reminder.target_date.toordinal() - reminder.lead_days
    )
    if (
        local.date() != expected_date
        or local.timetz().replace(tzinfo=None) != reminder.local_delivery_time
    ):
        raise BriefScannerReminderRuntimeError(
            "brief_scanner_reminder_runtime_schedule_invalid"
        )
    return mission_invocation, mission, reminder_invocation, reminder


def _same_mission(existing: dict[str, Any], expected: dict[str, Any]) -> bool:
    due = existing.get("due_at")
    if isinstance(due, datetime):
        due = due.date()
    if isinstance(due, date):
        due = due.isoformat()
    metadata = dict(existing.get("metadata") or {})
    return (
        str(existing.get("mission_id") or "") == expected["mission_id"]
        and str(existing.get("phone_hash") or "") == expected["phone_hash"]
        and str(existing.get("title") or "") == expected["title"]
        and str(existing.get("topic") or "") == expected["topic"]
        and str(existing.get("next_step") or "") == expected["next_step"]
        and (due or None) == expected["due_at"]
        and metadata == expected["metadata"]
    )


def _same_reminder(existing: dict[str, Any], expected: dict[str, Any]) -> bool:
    scheduled = existing.get("scheduled_at")
    if not isinstance(scheduled, datetime):
        try:
            scheduled = datetime.fromisoformat(str(scheduled))
        except (TypeError, ValueError):
            return False
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    return (
        str(existing.get("reminder_id") or "") == expected["reminder_id"]
        and str(existing.get("dedupe_key") or "") == expected["dedupe_key"]
        and str(existing.get("phone_hash") or "") == expected["phone_hash"]
        and str(existing.get("mission_id") or "") == expected["mission_id"]
        and str(existing.get("title") or "") == expected["title"]
        and str(existing.get("language") or "") == expected["language"]
        and str(existing.get("timezone") or "") == expected["timezone"]
        and scheduled.astimezone(UTC).isoformat() == expected["scheduled_at"]
    )


class BriefScannerMissionReminderRuntimeExecutor:
    """Persist an authorized Mission and encrypted Reminder as one idempotent unit."""

    def __init__(self, store: Any, *, phone: str) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name not in {"json", "postgresql"}:
            raise BriefScannerReminderRuntimeError(
                "brief_scanner_reminder_runtime_backend_invalid"
            )
        self.phone = _require_phone(phone)
        self.tenant_hash = _phone_hash(self.phone)

    def _records(
        self,
        mission_invocation: BriefScannerRuntimeInvocation,
        mission_command: BriefScannerMissionCommand,
        reminder_invocation: BriefScannerRuntimeInvocation,
        reminder_command: BriefScannerReminderCommand,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        mission_id = _stable_id(
            "hero-mission-v1",
            self.tenant_hash,
            mission_invocation.idempotency_key,
        )
        reminder_id = _stable_id(
            "hero-reminder-v1",
            self.tenant_hash,
            reminder_invocation.idempotency_key,
        )
        now = _now()
        mission = {
            "mission_id": mission_id,
            "phone_hash": self.tenant_hash,
            "title": mission_command.title,
            "topic": mission_command.topic,
            "status": "open",
            "last_action": "",
            "next_step": mission_command.next_step,
            "due_at": (
                mission_command.due_date.isoformat()
                if mission_command.due_date
                else None
            ),
            "metadata": {
                "source": mission_command.source,
                "category": mission_command.mission_kind.value,
            },
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        scheduled = reminder_command.scheduled_at_utc.astimezone(UTC)
        dedupe_material = (
            "brief-scanner-reminder-v1:"
            f"{self.tenant_hash}:{reminder_invocation.idempotency_key}"
        )
        reminder = {
            "reminder_id": reminder_id,
            "dedupe_key": hashlib.sha256(
                dedupe_material.encode("utf-8")
            ).hexdigest(),
            "phone_hash": self.tenant_hash,
            "recipient_ciphertext": encrypt_recipient(self.phone),
            "mission_id": mission_id,
            "title": reminder_command.title,
            "language": reminder_command.source_language,
            "timezone": reminder_command.timezone_name,
            "scheduled_at": scheduled.isoformat(),
            "status": "pending",
            "attempt_count": 0,
            "last_error": "",
            "next_attempt_at": scheduled.isoformat(),
            "lease_until": None,
            "sent_at": None,
            "created_at": now,
            "updated_at": now,
        }
        return mission, reminder

    def _execute_json(
        self,
        mission: dict[str, Any],
        reminder: dict[str, Any],
    ) -> BriefScannerMissionReminderStatus:
        def persist(data: dict[str, Any]) -> BriefScannerMissionReminderStatus:
            cases = data.setdefault("cases", {})
            reminders = data.setdefault("reminders", {})
            existing_mission = cases.get(mission["mission_id"])
            existing_reminder = reminders.get(reminder["reminder_id"])
            if (existing_mission is None) != (existing_reminder is None):
                raise BriefScannerReminderRuntimeError(
                    "brief_scanner_reminder_runtime_partial_state"
                )
            if existing_mission is not None:
                if not _same_mission(existing_mission, mission) or not _same_reminder(
                    existing_reminder,
                    reminder,
                ):
                    raise BriefScannerReminderRuntimeError(
                        "brief_scanner_reminder_runtime_idempotency_conflict"
                    )
                return BriefScannerMissionReminderStatus.REPLAYED
            cases[mission["mission_id"]] = deepcopy(mission)
            reminders[reminder["reminder_id"]] = deepcopy(reminder)
            return BriefScannerMissionReminderStatus.CREATED

        return self.store._transaction(persist)

    def _execute_postgres(
        self,
        mission: dict[str, Any],
        reminder: dict[str, Any],
    ) -> BriefScannerMissionReminderStatus:
        with self.store.pool.connection() as connection:
            mission_row = connection.execute(
                """
                INSERT INTO hero_missions
                    (mission_id, phone_hash, title, topic, status, last_action,
                     next_step, due_at, metadata)
                VALUES (%s, %s, %s, %s, 'open', '', %s, %s, %s)
                ON CONFLICT (mission_id) DO NOTHING
                RETURNING mission_id
                """,
                (
                    mission["mission_id"],
                    mission["phone_hash"],
                    mission["title"],
                    mission["topic"],
                    mission["next_step"],
                    mission["due_at"],
                    Jsonb(mission["metadata"]),
                ),
            ).fetchone()
            reminder_row = connection.execute(
                """
                INSERT INTO hero_reminders
                    (reminder_id, dedupe_key, phone_hash, recipient_ciphertext,
                     mission_id, title, language, timezone, scheduled_at, next_attempt_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dedupe_key) DO NOTHING
                RETURNING reminder_id
                """,
                (
                    reminder["reminder_id"],
                    reminder["dedupe_key"],
                    reminder["phone_hash"],
                    reminder["recipient_ciphertext"],
                    reminder["mission_id"],
                    reminder["title"],
                    reminder["language"],
                    reminder["timezone"],
                    reminder["scheduled_at"],
                    reminder["next_attempt_at"],
                ),
            ).fetchone()
            if (mission_row is None) != (reminder_row is None):
                raise BriefScannerReminderRuntimeError(
                    "brief_scanner_reminder_runtime_partial_state"
                )
            stored_mission = connection.execute(
                "SELECT * FROM hero_missions WHERE mission_id = %s FOR UPDATE",
                (mission["mission_id"],),
            ).fetchone()
            stored_reminder = connection.execute(
                "SELECT * FROM hero_reminders WHERE dedupe_key = %s FOR UPDATE",
                (reminder["dedupe_key"],),
            ).fetchone()
            if not stored_mission or not stored_reminder:
                raise BriefScannerReminderRuntimeError(
                    "brief_scanner_reminder_runtime_persistence_invalid"
                )
            if not _same_mission(
                dict(stored_mission),
                mission,
            ) or not _same_reminder(dict(stored_reminder), reminder):
                raise BriefScannerReminderRuntimeError(
                    "brief_scanner_reminder_runtime_idempotency_conflict"
                )
            return (
                BriefScannerMissionReminderStatus.CREATED
                if mission_row is not None
                else BriefScannerMissionReminderStatus.REPLAYED
            )

    def __call__(
        self,
        batch: BriefScannerRuntimeBatch,
    ) -> BriefScannerMissionReminderResult:
        (
            mission_invocation,
            mission_command,
            reminder_invocation,
            reminder_command,
        ) = _require_batch(batch)
        mission, reminder = self._records(
            mission_invocation,
            mission_command,
            reminder_invocation,
            reminder_command,
        )
        status = (
            self._execute_postgres(mission, reminder)
            if self.backend_name == "postgresql"
            else self._execute_json(mission, reminder)
        )
        return BriefScannerMissionReminderResult(
            status=status,
            planning_fingerprint=batch.planning_fingerprint,
            mission_id=mission["mission_id"],
            reminder_id=reminder["reminder_id"],
            executed_actions=(
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentAction.CREATE_REMINDER,
            ),
        )
