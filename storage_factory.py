"""Production storage selection with explicit fail-closed PostgreSQL policy."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from config import APP_VERSION
from data_store import JsonDataStore, PostgresDataStore
from database_migrations import SchemaMigrationError, run_database_migrations

logger = logging.getLogger("amthero24.storage_policy")
_POLICY_INSTALLED = False


class StorageInitializationError(RuntimeError):
    """Raised when the configured durable production store cannot start safely."""


def database_fallback_allowed() -> bool:
    """Return whether an operator explicitly allowed temporary JSON fallback.

    The default is false. A configured PostgreSQL service therefore fails closed
    instead of silently accepting traffic into a divergent ephemeral JSON store.
    """
    return os.getenv("DATABASE_FALLBACK_ALLOWED", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _direct_json_store(path: str | Path) -> JsonDataStore:
    """Construct the JSON implementation without re-running backend auto-selection."""
    store = object.__new__(JsonDataStore)
    JsonDataStore.__init__(store, path)
    return store


def _close_postgres(store: PostgresDataStore | None) -> None:
    if store is None:
        return
    try:
        store.close()
    except Exception:
        logger.exception("Unable to close rejected PostgreSQL pool")


def create_runtime_store(path: str | Path) -> Any:
    """Create production storage, migrate it under lock, and never split durable state.

    With no DATABASE_URL, JSON remains available for local development and tests. With
    DATABASE_URL, PostgreSQL is mandatory unless the operator explicitly activates the
    temporary fallback. Schema incompatibility never falls back because that would hide a
    deployment/version error and create divergent writes.
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return _direct_json_store(path)

    postgres: PostgresDataStore | None = None
    try:
        postgres = PostgresDataStore(database_url)
        run_database_migrations(postgres, app_version=APP_VERSION)
        postgres.migrate_json(path)
        logger.info("Using required PostgreSQL storage backend with current schema")
        return postgres
    except SchemaMigrationError as exc:
        _close_postgres(postgres)
        logger.critical("PostgreSQL schema migration rejected startup", extra={"code": exc.code})
        raise StorageInitializationError(
            "Configured PostgreSQL schema is incompatible; refusing application startup"
        ) from exc
    except Exception as exc:
        _close_postgres(postgres)
        if database_fallback_allowed():
            logger.exception(
                "PostgreSQL unavailable; explicit emergency JSON fallback is active"
            )
            return _direct_json_store(path)
        logger.critical(
            "PostgreSQL initialization failed and JSON fallback is disabled",
            exc_info=True,
        )
        raise StorageInitializationError(
            "Configured PostgreSQL storage is unavailable; refusing unsafe JSON fallback"
        ) from exc


def _production_new(cls: type[JsonDataStore], path: str | Path) -> Any:
    if cls is JsonDataStore:
        return create_runtime_store(path)
    return object.__new__(cls)


def install_production_storage_policy() -> None:
    """Install the strict selector before importing the production ASGI composition.

    Unit tests and local modules that import `app` directly keep the historical JSON
    behavior. The deployed `webhook_security:app` entrypoint installs this policy first,
    so a configured production database can never silently diverge to JSON.
    """
    global _POLICY_INSTALLED
    if _POLICY_INSTALLED:
        return
    JsonDataStore.__new__ = _production_new  # type: ignore[method-assign]
    _POLICY_INSTALLED = True
