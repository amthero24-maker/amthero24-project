"""Multi-replica PostgreSQL DDL coordination for AmtHero24.

The advisory lock is a fixed application constant. It is not derived from a user,
message, deployment identifier, database URL, hostname, or secret. Every schema owner
uses the same lock so overlapping Railway replicas cannot execute application DDL at the
same time.
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable
from typing import Any

_SCHEMA_LOCK_NAMESPACE = 0x414D5448  # "AMTH", signed int32-safe
_SCHEMA_LOCK_RESOURCE = 0x45524F32  # "ERO2", signed int32-safe
_COMPONENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


class SchemaCoordinationError(RuntimeError):
    """Raised when application DDL cannot acquire or safely release its lock."""


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


def _locked(row: Any) -> bool:
    if row is None:
        return False
    if hasattr(row, "get"):
        return bool(row.get("locked"))
    try:
        return bool(row[0])
    except (IndexError, KeyError, TypeError):
        return False


def _safe_component(component: str) -> str:
    value = str(component or "").strip().casefold()
    if not _COMPONENT_PATTERN.fullmatch(value):
        raise SchemaCoordinationError("Invalid schema component name")
    return value


def _release(connection: Any) -> None:
    """Release the session lock even after a failed DDL transaction."""
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
        raise SchemaCoordinationError("Unable to release schema coordination lock") from exc
    if row is not None and hasattr(row, "get") and not bool(row.get("unlocked")):
        raise SchemaCoordinationError("Schema coordination lock was not owned")


def run_schema_statements(
    store: Any,
    component: str,
    statements: Iterable[str],
    *,
    record: bool = True,
) -> tuple[str, ...]:
    """Execute one component's idempotent DDL under the global application lock."""
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return ()
    name = _safe_component(component)
    prepared = tuple(str(statement or "").strip() for statement in statements if str(statement or "").strip())
    if not prepared:
        return ()

    deadline = time.monotonic() + schema_lock_timeout_seconds()
    acquired = False
    with store.pool.connection() as connection:
        while not acquired:
            try:
                row = connection.execute(
                    "SELECT pg_try_advisory_lock(%s, %s) AS locked",
                    (_SCHEMA_LOCK_NAMESPACE, _SCHEMA_LOCK_RESOURCE),
                ).fetchone()
                acquired = _locked(row)
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
            for statement in prepared:
                connection.execute(statement)
            if record:
                connection.execute(
                    """
                    INSERT INTO schema_migrations (name)
                    VALUES (%s)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (f"schema_bootstrap:{name}:v1",),
                )
            connection.commit()
        except Exception:
            try:
                _release(connection)
            except SchemaCoordinationError:
                pass
            raise
        _release(connection)
    return prepared


def schema_coordination_status(store: Any) -> str:
    return "coordinated" if str(getattr(store, "backend_name", "json")) == "postgresql" else "not_required"
