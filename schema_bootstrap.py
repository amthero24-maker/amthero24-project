"""Idempotent startup bootstrap for all PostgreSQL-backed repositories."""
from __future__ import annotations

from typing import Any

from abuse_guard import AbuseGuardRepository
from closed_beta_admission_repository import ClosedBetaAdmissionRepository
from document_action_repository import PendingDocumentRepository
from durable_queue import DurableQueueRepository
from entitlement_engine import EntitlementRepository
from feedback_engine import FeedbackRepository
from hero_memory import HeroMemory
from outbound_delivery import OutboundDeliveryRepository
from privacy_engine import initialize_privacy_schema
from provider_reliability import ProviderReliabilityRepository
from reminder_engine import ReminderRepository
from support_handoff import SupportRepository

_COMPONENTS: tuple[tuple[str, type[Any]], ...] = (
    ("hero_memory", HeroMemory),
    ("reminders", ReminderRepository),
    ("pending_documents", PendingDocumentRepository),
    ("entitlements", EntitlementRepository),
    ("abuse_guard", AbuseGuardRepository),
    ("provider_reliability", ProviderReliabilityRepository),
    ("human_support", SupportRepository),
    ("anonymous_feedback", FeedbackRepository),
    ("outbound_delivery", OutboundDeliveryRepository),
    ("durable_queue", DurableQueueRepository),
    ("closed_beta_admission", ClosedBetaAdmissionRepository),
)


def schema_component_names() -> tuple[str, ...]:
    return tuple(name for name, _repository_type in _COMPONENTS) + ("privacy",)


def bootstrap_postgres_schemas(store: Any) -> tuple[str, ...]:
    """Create every production schema on every PostgreSQL startup.

    Repository constructors are idempotent. Components that expose ``schema_ready``
    must confirm it; otherwise startup fails closed instead of advertising a partial
    production schema.
    """
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return ()
    initialized: list[str] = []
    for name, repository_type in _COMPONENTS:
        repository = repository_type(store)
        if getattr(repository, "schema_ready", True) is not True:
            raise RuntimeError(f"schema_component_unavailable:{name}")
        initialized.append(name)
    initialize_privacy_schema(store)
    initialized.append("privacy")
    return tuple(initialized)
