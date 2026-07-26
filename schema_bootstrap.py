"""Central PostgreSQL schema bootstrap for every production composition layer.

Repository constructors remain responsible for their own idempotent DDL. This module
ensures optional features have created their tables before health/admin endpoints or
privacy deletion can reference them, even when no user has exercised that feature yet.
"""
from __future__ import annotations

from typing import Any

from abuse_guard import AbuseGuardRepository
from document_action_repository import PendingDocumentRepository
from durable_queue import DurableQueueRepository
from entitlement_engine import EntitlementRepository
from feedback_engine import FeedbackRepository
from hero_memory import HeroMemory
from message_idempotency import MessageClaimRepository
from provider_reliability import ProviderReliabilityRepository
from reminder_engine import ReminderRepository
from support_handoff import SupportRepository


def bootstrap_postgres_schemas(store: Any) -> tuple[str, ...]:
    """Create all current production tables idempotently and return component names."""
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return ()

    components: tuple[tuple[str, type[Any]], ...] = (
        ("hero_memory", HeroMemory),
        ("message_idempotency", MessageClaimRepository),
        ("durable_inbound_queue", DurableQueueRepository),
        ("reminders", ReminderRepository),
        ("pending_documents", PendingDocumentRepository),
        ("entitlements", EntitlementRepository),
        ("abuse_guard", AbuseGuardRepository),
        ("provider_reliability", ProviderReliabilityRepository),
        ("human_support", SupportRepository),
        ("anonymous_feedback", FeedbackRepository),
    )
    initialized: list[str] = []
    for name, repository_type in components:
        repository_type(store)
        initialized.append(name)
    return tuple(initialized)
