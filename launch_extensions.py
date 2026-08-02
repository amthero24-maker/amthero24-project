"""Protected Beta launch report composed above production reliability layers."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

import admin_extensions as admin_module
import provider_extensions as composed
from config import APP_VERSION, GROQ_MODEL
from launch_readiness import build_launch_report

core = composed.core


def _unavailable(code: str) -> JSONResponse:
    """Return a bounded stage code without exposing exception or production data."""
    return JSONResponse(
        {"status": code},
        status_code=500,
        headers={"Cache-Control": "no-store"},
    )


def _overview_failure_code(exc: Exception) -> str:
    """Classify failures without reflecting exception messages or database identifiers."""
    name = type(exc).__name__.casefold()
    if name in {"undefinedtable", "undefinedcolumn"}:
        return "overview_database_schema_error"
    if name in {"databaseerror", "operationalerror", "interfaceerror", "integrityerror", "programmingerror"}:
        return "overview_database_query_error"
    if isinstance(exc, (TypeError, ValueError, KeyError, IndexError)):
        return "overview_data_shape_error"
    return "overview_unexpected_error"


@core.app.get("/admin/launch-readiness", include_in_schema=False)
async def launch_readiness(request: Request) -> JSONResponse:
    denied = admin_module._authorize(request)
    if denied is not None:
        return denied
    try:
        overview = admin_module.build_overview(core.store, version=APP_VERSION, model=GROQ_MODEL)
    except Exception as exc:
        return _unavailable(_overview_failure_code(exc))
    try:
        report = build_launch_report(overview)
    except Exception:
        return _unavailable("launch_report_build_failed")
    if admin_module.contains_personal_fields(report):
        return _unavailable("personal_field_guard_failed")
    return JSONResponse(report, headers={"Cache-Control": "no-store"})


app = composed.app
store = composed.store
