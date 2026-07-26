"""Top-level provider telemetry and reliability composition."""
from __future__ import annotations

import os
import time
from typing import Any

import abuse_extensions as composed
import admin_extensions as admin_module
import privacy_engine as privacy_module
import reminder_extensions as reminder_module
from provider_reliability import (
    ProviderCircuitOpen,
    ProviderReliabilityRepository,
    circuit_enabled,
    elapsed_ms,
    telemetry_enabled,
)

core = composed.core
_ORIGINAL_GENERATE_REPLY = core.generate_reply
_ORIGINAL_SEND_TEXT = core.send_whatsapp_message
_ORIGINAL_SEND_TEMPLATE = reminder_module.send_whatsapp_template
_ORIGINAL_PRIVACY_CLEANUP = privacy_module.cleanup_retention
_ORIGINAL_ADMIN_BUILD_OVERVIEW = admin_module.build_overview
_PROVIDER_REPOSITORY: ProviderReliabilityRepository | None = None


def _repository(store: Any | None = None) -> ProviderReliabilityRepository:
    global _PROVIDER_REPOSITORY
    target = store or core.store
    if _PROVIDER_REPOSITORY is None or _PROVIDER_REPOSITORY.store is not target:
        _PROVIDER_REPOSITORY = ProviderReliabilityRepository(target)
    return _PROVIDER_REPOSITORY


def _error_code(exc: Exception) -> str:
    return type(exc).__name__[:80]


def generate_reply(**kwargs: Any) -> str:
    repository = _repository()
    decision = repository.before_call("groq")
    if not decision.allowed:
        repository.record("groq", "generate_reply", "circuit_rejected", 0, error_code="circuit_open")
        raise ProviderCircuitOpen("Groq is temporarily unavailable")
    started = time.perf_counter()
    try:
        result = _ORIGINAL_GENERATE_REPLY(**kwargs)
    except Exception as exc:
        repository.record(
            "groq", "generate_reply", "failure", elapsed_ms(started), error_code=_error_code(exc)
        )
        raise
    repository.record("groq", "generate_reply", "success", elapsed_ms(started))
    return result


async def send_whatsapp_message(to: str, text: str) -> list[dict]:
    started = time.perf_counter()
    try:
        result = await _ORIGINAL_SEND_TEXT(to, text)
    except Exception as exc:
        _repository().record(
            "whatsapp", "send_text", "failure", elapsed_ms(started), error_code=_error_code(exc)
        )
        raise
    _repository().record("whatsapp", "send_text", "success", elapsed_ms(started))
    return result


async def send_whatsapp_template(
    to: str,
    template_name: str,
    language_code: str,
    body_parameters: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await _ORIGINAL_SEND_TEMPLATE(to, template_name, language_code, body_parameters)
    except Exception as exc:
        _repository().record(
            "whatsapp", "send_template", "failure", elapsed_ms(started), error_code=_error_code(exc)
        )
        raise
    _repository().record("whatsapp", "send_template", "success", elapsed_ms(started))
    return result


def _cleanup_retention(store: Any, **kwargs: Any) -> dict[str, int]:
    result = _ORIGINAL_PRIVACY_CLEANUP(store, **kwargs)
    result["provider_events"] = _repository(store).cleanup(
        now=kwargs.get("now"),
        retention_days=int(os.getenv("PROVIDER_EVENT_RETENTION_DAYS", "30")),
    )
    return result


def _build_overview(store: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _ORIGINAL_ADMIN_BUILD_OVERVIEW(store, **kwargs)
    payload["providers"] = _repository(store).aggregate(now=kwargs.get("now"))
    return payload


def provider_status() -> dict[str, str]:
    repository = _repository()
    return {
        "telemetry": "enabled" if telemetry_enabled() else "disabled",
        "groq_circuit": "disabled" if not circuit_enabled() else repository.circuit_status("groq"),
    }


_repository()
core.generate_reply = generate_reply
core.send_whatsapp_message = send_whatsapp_message
reminder_module.send_whatsapp_template = send_whatsapp_template
privacy_module.cleanup_retention = _cleanup_retention
admin_module.build_overview = _build_overview

app = composed.app
store = composed.store
