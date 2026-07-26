"""Safe liveness/readiness diagnostics and production entrypoint."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

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


# Import the fully composed application only after defining pure health helpers.
from application import app, store  # noqa: E402
from config import APP_VERSION, GROQ_MODEL  # noqa: E402


@app.get("/ready", include_in_schema=False)
async def ready() -> JSONResponse:
    payload, status_code = readiness_payload(store, version=APP_VERSION, model=GROQ_MODEL)
    return JSONResponse(payload, status_code=status_code)
