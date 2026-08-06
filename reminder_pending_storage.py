"""Register privacy-safe, session-scoped reminder clarification fields.

The central data-store allowlist intentionally rejects unknown profile keys. Reminder
clarification therefore registers its bounded fields before the production app is
composed. They expire with the existing session lifecycle and contain only a short
subject, an ISO timestamp, and small recurrence controls.
"""
from __future__ import annotations

import data_store

PENDING_REMINDER_FIELDS = {
    "pending_reminder_at",
    "pending_reminder_title",
    "pending_reminder_recurrence_days",
    "pending_reminder_recurrence_count",
    "pending_reminder_weekdays_only",
    "pending_reminder_weekdays",
}

data_store._ALLOWED_USER_FIELDS.update(PENDING_REMINDER_FIELDS)
data_store._SESSION_FIELDS.update(PENDING_REMINDER_FIELDS)
data_store._FIELD_LIMITS.setdefault("pending_reminder_at", 64)
data_store._FIELD_LIMITS.setdefault("pending_reminder_title", 180)
data_store._FIELD_LIMITS.setdefault("pending_reminder_recurrence_days", 3)
data_store._FIELD_LIMITS.setdefault("pending_reminder_recurrence_count", 3)
data_store._FIELD_LIMITS.setdefault("pending_reminder_weekdays_only", 1)
data_store._FIELD_LIMITS.setdefault("pending_reminder_weekdays", 13)
