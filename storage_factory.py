"""Production storage selection with explicit fail-closed PostgreSQL policy."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from data_store import JsonDataStore, PostgresDataStore

logger = logging.getLogger("amthero24.storage_policy")


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


def create_runtime_store(path: str | Path) -> Any:
    """Create the production store and never silently split durable state.

    With no DATABASE_URL, JSON remains available for local development and tests.
    With DATABASE_URL, PostgreSQL is mandatory unless the operator explicitly sets
    DATABASE_FALLBACK_ALLOWED=true. Even then, `/ready` remains not ready because
    the backend is a JSON fallback while a database was expected.
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return _direct_json_store(path)

    try:
        postgres = PostgresDataStore(database_url)
        postgres.migrate_json(path)
        logger.info("Using required PostgreSQL storage backend")
        return postgres
    except Exception as exc:
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
