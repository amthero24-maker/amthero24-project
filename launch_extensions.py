"""Protected Beta launch report composed above production reliability layers."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

import admin_extensions as admin_module
import provider_extensions as composed
from config import APP_VERSION, GROQ_MODEL
from launch_readiness import build_launch_report

core = composed.core


@core.app.get("/admin/launch-readiness", include_in_schema=False)
async def launch_readiness(request: Request) -> JSONResponse:
    denied = admin_module._authorize(request)
    if denied is not None:
        return denied
    overview = admin_module.build_overview(core.store, version=APP_VERSION, model=GROQ_MODEL)
    report = build_launch_report(overview)
    if admin_module.contains_personal_fields(report):
        return JSONResponse(
            {"status": "unavailable"},
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(report, headers={"Cache-Control": "no-store"})


app = composed.app
store = composed.store
