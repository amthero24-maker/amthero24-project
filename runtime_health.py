"""Safe liveness/readiness diagnostics for AmtHero24 production."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_REQUIRED_RUNTIME_ENV = (
    "GROQ_API_KEY",
    "WHATSAPP_TOKEN",
    "PHONE_NUMBER_ID",
    "VERIFY_TOKEN",
)


def configuration_ready() -> bool:
    """Return whether required runtime settings are present without exposing them."""
    return all(bool(os.getenv(name, "").strip()) for name in _REQUIRED_RUNTIME_ENV)


def storage_ready(store: Any) -> tuple[bool, str]:
    """Verify the selected storage backend with a lightweight operation."""
    backend = str(getattr(store, "backend_name", "unknown"))
    database_expected = bool(os.getenv("DATABASE_URL", "").strip())

    try:
        if backend == "postgresql":
            with store.pool.connection(timeout=5) as connection:
                row = connection.execute("SELECT 1 AS healthy").fetchone()
            healthy = bool(row and (row.get("healthy") if hasattr(row, "get") else row[0]) == 1)
            return healthy, backend

        if backend == "json":
            if database_expected:
                # Railway is configured for PostgreSQL but the application silently
                # fell back to ephemeral JSON. Keep liveness up but fail readiness.
                return False, "json-fallback"
            path = Path(getattr(store, "path", "data/store.json"))
            path.parent.mkdir(parents=True, exist_ok=True)
            return os.access(path.parent, os.W_OK), backend
    except Exception:
        return False, backend

    return False, backend


def readiness_payload(store: Any, *, version: str, model: str) -> tuple[dict[str, object], int]:
    config_ok = configuration_ready()
    storage_ok, backend = storage_ready(store)
    ready = config_ok and storage_ok
    payload: dict[str, object] = {
        "status": "ready" if ready else "not_ready",
        "version": version,
        "components": {
            "configuration": "ok" if config_ok else "missing",
            "storage": "ok" if storage_ok else "unavailable",
            "storage_backend": backend,
            "text_model": model,
        },
    }
    return payload, 200 if ready else 503
