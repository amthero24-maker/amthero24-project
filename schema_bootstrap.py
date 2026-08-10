"""Central PostgreSQL schema bootstrap for every production composition layer.

Repository constructors remain responsible for their own idempotent DDL. Production now
runs this bootstrap inside the versioned migration lock before application composition;
direct local/test construction keeps the same callable for compatibility.
"""
from __future__ import annotations

from typing import Any

from abuse_guard import AbuseGuardRepository
from closed_beta_admission_repository import ClosedBetaAdmissionRepository
from document_action_repository import PendingDocumentRepository
from durable_queue import DurableQueueRepository
from entitlement_engine import EntitlementRepository
from feedback_engine import FeedbackRepository
from hero_memory import HeroMemory
from message_idempotency import MessageClaimRepository
from outbound_delivery import OutboundDeliveryRepository
from provider_reliability import ProviderReliabilityRepository
from reminder_engine import ReminderRepository
from support_handoff import SupportRepository

_COMPONENTS: tuple[tuple[str, type[Any]], ...] = (
    ("hero_memory", HeroMemory),
    ("message_idempotency", MessageClaimRepository),
    ("durable_inbound_queue", DurableQueueRepository),
    ("outbound_delivery", OutboundDeliveryRepository),
    ("reminders", ReminderRepository),
    ("pending_documents", PendingDocumentRepository),
    ("entitlements", EntitlementRepository),
    ("abuse_guard", AbuseGuardRepository),
    ("provider_reliability", ProviderReliabilityRepository),
    ("human_support", SupportRepository),
    ("anonymous_feedback", FeedbackRepository),
    ("closed_beta_admission", ClosedBetaAdmissionRepository),
)


def schema_component_names() -> tuple[str, ...]:
    """Return static subsystem names without touching PostgreSQL or application data."""
    return tuple(name for name, _repository_type in _COMPONENTS)


def bootstrap_postgres_schemas(store: Any) -> tuple[str, ...]:
    """Create all current production tables idempotently and return component names."""
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return ()

    initialized: list[str] = []
    for name, repository_type in _COMPONENTS:
        repository = repository_type(store)
        if getattr(repository, "schema_ready", True) is not True:
            raise RuntimeError(f"schema_component_unavailable:{name}")
        initialized.append(name)
    return tuple(initialized)
