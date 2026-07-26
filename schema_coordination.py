"""Multi-replica PostgreSQL DDL coordination for AmtHero24.

The advisory lock is a fixed application constant. It is not derived from a user,
message, deployment identifier, database URL, hostname, or secret. Production installs
this policy before application composition, so base storage and every repository engine
share one global DDL lock during overlapping Railway starts.
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

_SCHEMA_LOCK_NAMESPACE = 0x414D5448  # "AMTH", signed int32-safe
_SCHEMA_LOCK_RESOURCE = 0x45524F32  # "ERO2", signed int32-safe
_COMPONENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_POLICY_INSTALLED = False
_INSTALLED_COMPONENTS: tuple[str, ...] = ()


class SchemaCoordinationError(RuntimeError):
    """Raised when application DDL cannot be coordinated safely."""


def schema_lock_timeout_seconds() -> float:
    try:
        value = float(os.getenv("SCHEMA_LOCK_TIMEOUT_SECONDS", "60").strip())
    except ValueError:
        value = 60.0
    return min(max(value, 5.0), 300.0)


def schema_lock_poll_seconds() -> float:
    try:
        value = float(os.getenv("SCHEMA_LOCK_POLL_SECONDS", "0.1").strip())
    except ValueError:
        value = 0.1
    return min(max(value, 0.02), 1.0)


def _safe_component(component: str) -> str:
    value = str(component or "").strip().casefold()
    if not _COMPONENT_PATTERN.fullmatch(value):
        raise SchemaCoordinationError("Invalid schema component name")
    return value


def _value(row: Any, key: str) -> bool:
    if row is None:
        return False
    if hasattr(row, "get"):
        return bool(row.get(key))
    try:
        return bool(row[0])
    except (IndexError, KeyError, TypeError):
        return False


def _close_broken(connection: Any) -> None:
    try:
        connection.close()
    except Exception:
        pass


def _release_lock(connection: Any) -> None:
    try:
        connection.rollback()
    except Exception:
        pass
    try:
        row = connection.execute(
            "SELECT pg_advisory_unlock(%s, %s) AS unlocked",
            (_SCHEMA_LOCK_NAMESPACE, _SCHEMA_LOCK_RESOURCE),
        ).fetchone()
        connection.commit()
    except Exception as exc:
        _close_broken(connection)
        raise SchemaCoordinationError("Unable to release schema coordination lock") from exc
    if not _value(row, "unlocked"):
        _close_broken(connection)
        raise SchemaCoordinationError("Schema coordination lock was not owned")


@contextmanager
def schema_lock(store: Any) -> Iterator[Any | None]:
    """Hold the global application DDL lock for one bounded critical section."""
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        yield None
        return

    deadline = time.monotonic() + schema_lock_timeout_seconds()
    acquired = False
    with store.pool.connection() as connection:
        while not acquired:
            try:
                row = connection.execute(
                    "SELECT pg_try_advisory_lock(%s, %s) AS locked",
                    (_SCHEMA_LOCK_NAMESPACE, _SCHEMA_LOCK_RESOURCE),
                ).fetchone()
                acquired = _value(row, "locked")
                connection.commit()
            except Exception as exc:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise SchemaCoordinationError("Unable to request schema coordination lock") from exc
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise SchemaCoordinationError("Timed out waiting for schema coordination lock")
            time.sleep(schema_lock_poll_seconds())

        try:
            yield connection
        except BaseException:
            _release_lock(connection)
            raise
        else:
            _release_lock(connection)


def _record_component(store: Any, component: str) -> None:
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return
    name = _safe_component(component)
    with store.pool.connection() as connection:
        connection.execute(
            """
            INSERT INTO schema_migrations (name)
            VALUES (%s)
            ON CONFLICT (name) DO NOTHING
            """,
            (f"schema_bootstrap:{name}:v1",),
        )


def _store_for(instance: Any) -> Any:
    return getattr(instance, "store", instance)


def _wrap_initializer(owner: type[Any], method_name: str, component: str) -> None:
    original = getattr(owner, method_name, None)
    if not callable(original):
        raise SchemaCoordinationError(f"Missing schema initializer for {component}")
    if getattr(original, "_amthero24_schema_coordinated", False):
        return

    @wraps(original)
    def coordinated(instance: Any, *args: Any, **kwargs: Any) -> Any:
        store = _store_for(instance)
        with schema_lock(store):
            result = original(instance, *args, **kwargs)
            _record_component(store, component)
            return result

    setattr(coordinated, "_amthero24_schema_coordinated", True)
    setattr(owner, method_name, coordinated)


def _wrap_constructor(owner: type[Any], component: str) -> None:
    original = owner.__init__
    if getattr(original, "_amthero24_schema_coordinated", False):
        return

    @wraps(original)
    def coordinated(instance: Any, store: Any, *args: Any, **kwargs: Any) -> None:
        with schema_lock(store):
            original(instance, store, *args, **kwargs)
            _record_component(store, component)

    setattr(coordinated, "_amthero24_schema_coordinated", True)
    owner.__init__ = coordinated  # type: ignore[method-assign]


def _initializer_name(owner: type[Any]) -> str | None:
    for candidate in ("_initialize_postgres_schema", "_initialize_schema", "_init_postgres_schema"):
        if callable(getattr(owner, candidate, None)):
            return candidate
    return None


def install_schema_coordination() -> tuple[str, ...]:
    """Wrap base storage and every production repository before they are instantiated."""
    global _POLICY_INSTALLED, _INSTALLED_COMPONENTS
    if _POLICY_INSTALLED:
        return _INSTALLED_COMPONENTS

    from abuse_guard import AbuseGuardRepository
    from data_store import PostgresDataStore
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

    owners: tuple[tuple[str, type[Any]], ...] = (
        ("base_storage", PostgresDataStore),
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
    )
    installed: list[str] = []
    for component, owner in owners:
        initializer = _initializer_name(owner)
        if initializer is None:
            _wrap_constructor(owner, component)
        else:
            _wrap_initializer(owner, initializer, component)
        installed.append(component)
    _INSTALLED_COMPONENTS = tuple(installed)
    _POLICY_INSTALLED = True
    return _INSTALLED_COMPONENTS


def schema_coordination_status(store: Any) -> str:
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return "not_required"
    return "coordinated" if _POLICY_INSTALLED else "uninstalled"


def installed_schema_components() -> tuple[str, ...]:
    return _INSTALLED_COMPONENTS
