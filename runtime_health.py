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


def _signature_required() -> bool:
    return os.getenv("WEBHOOK_SIGNATURE_REQUIRED", "false").strip().casefold() in {"1", "true", "yes", "on"}


def configuration_ready() -> bool:
    """Return whether required runtime settings are present without exposing them."""
    base_ready = all(bool(os.getenv(name, "").strip()) for name in _REQUIRED_RUNTIME_ENV)
    signature_ready = not _signature_required() or bool(os.getenv("META_APP_SECRET", "").strip())
    return base_ready and signature_ready


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
    required = _signature_required()
    secret_present = bool(os.getenv("META_APP_SECRET", "").strip())
    signature_status = "enforced" if secret_present else ("missing" if required else "optional")
    entitlement_enforced = os.getenv("ENTITLEMENT_ENFORCEMENT_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}
    abuse_enabled = os.getenv("ABUSE_GUARD_ENABLED", "true").strip().casefold() not in {"0", "false", "no", "off"}
    abuse_enforced = os.getenv("ABUSE_GUARD_ENFORCEMENT_ENABLED", "true").strip().casefold() in {"1", "true", "yes", "on"}
    provider = provider_layer.provider_status()
    admin_configured = bool(os.getenv("ADMIN_API_TOKEN", "").strip())
    support_enabled = os.getenv("HUMAN_SUPPORT_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}
    support_configured = support_enabled and bool(os.getenv("SUPPORT_API_TOKEN", "").strip()) and bool(os.getenv("SUPPORT_ENCRYPTION_KEY", "").strip())
    ready = config_ok and storage_ok
    payload: dict[str, object] = {
        "status": "ready" if ready else "not_ready",
        "version": version,
        "components": {
            "configuration": "ok" if config_ok else "missing",
            "storage": "ok" if storage_ok else "unavailable",
            "storage_backend": backend,
            "webhook_signature": signature_status,
            "text_model": model,
            "document_actions": "enabled",
            "reminders": "enabled" if os.getenv("REMINDER_WORKER_ENABLED", "true").strip().casefold() not in {"0", "false", "no", "off"} else "disabled",
            "reminder_template": "configured" if os.getenv("WHATSAPP_REMINDER_TEMPLATE", "").strip() else "service-window-only",
            "privacy_retention": "enabled" if os.getenv("PRIVACY_RETENTION_ENABLED", "true").strip().casefold() not in {"0", "false", "no", "off"} else "disabled",
            "admin_overview": "configured" if admin_configured else "disabled",
            "beta_launch_report": "configured" if admin_configured else "disabled",
            "human_support": "configured" if support_configured else ("misconfigured" if support_enabled else "disabled"),
            "entitlements": "enforced" if entitlement_enforced else "observe-only",
            "default_plan": os.getenv("ENTITLEMENT_DEFAULT_PLAN", "beta").strip().casefold() or "beta",
            "payments": "disabled",
            "abuse_guard": "disabled" if not abuse_enabled else ("enforced" if abuse_enforced else "observe-only"),
            "provider_telemetry": provider["telemetry"],
            "groq_circuit": provider["groq_circuit"],
        },
    }
    return payload, 200 if ready else 503


# Import all production composition layers after defining pure health helpers.
import provider_extensions as provider_layer  # noqa: E402
from support_extensions import app, store  # noqa: E402
from config import APP_VERSION, GROQ_MODEL  # noqa: E402


@app.get("/ready", include_in_schema=False)
async def ready() -> JSONResponse:
    payload, status_code = readiness_payload(store, version=APP_VERSION, model=GROQ_MODEL)
    return JSONResponse(payload, status_code=status_code)
