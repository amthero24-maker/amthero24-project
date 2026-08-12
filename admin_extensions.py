"""Protected operational overview for AmtHero24 administrators."""
from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import document_action_extensions as composed
from admin_metrics import build_overview, contains_personal_fields
from closed_beta_metrics import (
    build_closed_beta_metrics,
    contains_closed_beta_identifiers,
)
from config import APP_VERSION, GROQ_MODEL

core = composed.core


def admin_enabled() -> bool:
    return bool(os.getenv("ADMIN_API_TOKEN", "").strip())


def _provided_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if authorization.casefold().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("x-admin-token", "").strip()


def _authorize(request: Request) -> JSONResponse | None:
    configured = os.getenv("ADMIN_API_TOKEN", "").strip()
    if not configured:
        return JSONResponse({"status": "not_found"}, status_code=404, headers={"Cache-Control": "no-store"})
    supplied = _provided_token(request)
    if not supplied or not hmac.compare_digest(configured, supplied):
        return JSONResponse(
            {"status": "unauthorized"},
            status_code=401,
            headers={"Cache-Control": "no-store", "WWW-Authenticate": "Bearer"},
        )
    return None


def build_operator_overview() -> dict[str, object]:
    """Compose product and Closed Beta aggregates without user identifiers."""
    payload = build_overview(core.store, version=APP_VERSION, model=GROQ_MODEL)
    payload["closed_beta_admission"] = build_closed_beta_metrics(core.store)
    return payload


@core.app.get("/admin/overview", include_in_schema=False)
async def admin_overview(request: Request) -> JSONResponse:
    denied = _authorize(request)
    if denied is not None:
        return denied
    payload = await run_in_threadpool(build_operator_overview)
    if contains_personal_fields(payload) or contains_closed_beta_identifiers(payload):
        return JSONResponse(
            {"status": "unavailable"},
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


app = composed.app
store = composed.store
