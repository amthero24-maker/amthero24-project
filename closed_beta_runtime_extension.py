"""Final runtime wrapper for privacy-safe Closed Beta admission.

Installed after reminder/Sam composition. With admission disabled (the production
default) ordinary messages delegate directly to the previously composed path. User
privacy commands remain available without enabling admission.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any, Awaitable

from closed_beta_admission_repository import ClosedBetaAdmissionRepository
from closed_beta_admission_service import evaluate_beta_admission
from closed_beta_onboarding import onboarding_config
from closed_beta_privacy import (
    beta_left_message,
    beta_not_active_message,
    beta_privacy_unavailable_message,
    is_delete_request,
    is_leave_request,
    render_beta_export,
)

_CORE_MARKER = "_closed_beta_runtime_installed"
_READINESS_MARKER = "_closed_beta_runtime_readiness_installed"


def _runtime_environment() -> dict[str, str]:
    """Read only the documented Closed Beta controls from process configuration."""
    return {
        "CLOSED_BETA_ADMISSION_ENABLED": os.getenv(
            "CLOSED_BETA_ADMISSION_ENABLED", "false"
        ),
        "CLOSED_BETA_ADMISSION_CAPACITY": os.getenv(
            "CLOSED_BETA_ADMISSION_CAPACITY", "5"
        ),
        "CLOSED_BETA_ADMISSION_WAVE": os.getenv(
            "CLOSED_BETA_ADMISSION_WAVE", "wave1"
        ),
        "CLOSED_BETA_TENANT_KEY": os.getenv(
            "CLOSED_BETA_TENANT_KEY", "default"
        ),
        "CLOSED_BETA_NOTICE_VERSION": os.getenv(
            "CLOSED_BETA_NOTICE_VERSION", "2026-08-wave1-v1"
        ),
    }


def _language(core: Any, message: Any) -> str:
    profile = core.store.get_user(message.sender)
    memory_enabled = profile.get("memory_consent") == "granted"
    previous_language = str(
        profile.get("preferred_language") if memory_enabled else profile.get("session_language")
        or profile.get("preferred_language")
        or "de"
    )
    return (
        core.detect_language(message.text, previous_language)
        if str(message.text or "").strip()
        else previous_language
    )


def _repository(store: Any, env: Mapping[str, str]) -> ClosedBetaAdmissionRepository | None:
    try:
        config = onboarding_config(env)
        repository = ClosedBetaAdmissionRepository(
            store,
            tenant_key=config.tenant_key,
            wave=config.wave,
        )
    except (TypeError, ValueError):
        return None
    return repository if repository.schema_ready else None


def runtime_state(store: Any, env: Mapping[str, str]) -> tuple[bool, str]:
    """Return a public-safe readiness state without participant counts or IDs."""
    try:
        config = onboarding_config(env)
    except (TypeError, ValueError):
        return False, "misconfigured"
    if not config.enabled:
        return True, "disabled"
    repository = _repository(store, env)
    if repository is None:
        return False, "unavailable"
    status = repository.status(config.policy)
    if not status.verified:
        return False, "unavailable"
    return True, "full" if status.full else "open"


def _install_readiness(
    runtime_health: Any,
    env_provider: Callable[[], Mapping[str, str]],
) -> None:
    if getattr(runtime_health, _READINESS_MARKER, False):
        return
    original = runtime_health.readiness_payload

    def readiness_payload(*args: Any, **kwargs: Any) -> tuple[dict[str, object], int]:
        payload, status_code = original(*args, **kwargs)
        store = args[0] if args else kwargs.get("store")
        beta_ready, beta_state = runtime_state(store, env_provider())
        components = payload.get("components")
        if isinstance(components, dict):
            components["closed_beta_admission"] = beta_state
        if not beta_ready:
            payload["status"] = "not_ready"
            status_code = 503
        return payload, status_code

    runtime_health.readiness_payload = readiness_payload
    setattr(runtime_health, _READINESS_MARKER, True)


def install(
    core: Any,
    *,
    runtime_health: Any | None = None,
    env_provider: Callable[[], Mapping[str, str]] | None = None,
) -> None:
    """Install one idempotent wrapper around the already-composed app path."""
    provider = env_provider or _runtime_environment
    if runtime_health is not None:
        _install_readiness(runtime_health, provider)
    if getattr(core, _CORE_MARKER, False):
        return

    original: Callable[[Any], Awaitable[None]] = core.process_incoming

    async def process_incoming(message: Any) -> None:
        language = _language(core, message)
        text = str(message.text or "")
        commands_allowed = getattr(message, "internal_context", "") != "document_analysis"
        environment = dict(provider())

        if commands_allowed and is_delete_request(text):
            repository = _repository(core.store, environment)
            if repository is None:
                await core._finish(
                    message.message_id,
                    beta_privacy_unavailable_message(language),
                    message.sender,
                )
                return
            exported = repository.export_user_status(message.sender)
            if exported.get("status") != "available":
                await core._finish(
                    message.message_id,
                    beta_privacy_unavailable_message(language),
                    message.sender,
                )
                return
            records = exported.get("records", []) if isinstance(exported.get("records"), list) else []
            if records and not repository.delete_user(message.sender):
                await core._finish(
                    message.message_id,
                    beta_privacy_unavailable_message(language),
                    message.sender,
                )
                return
            try:
                core._hero_memory().delete_all_user_data(message.sender)
            except Exception:
                await core._finish(
                    message.message_id,
                    beta_privacy_unavailable_message(language),
                    message.sender,
                )
                return
            await core._finish(
                message.message_id,
                core._deletion_confirmation(language),
                message.sender,
            )
            return

        if commands_allowed and core._is_export_request(text):
            try:
                profile_export = core._hero_memory().export_user_data(message.sender)
                base_reply = core._export_reply(language, profile_export)
            except Exception:
                await core._finish(
                    message.message_id,
                    beta_privacy_unavailable_message(language),
                    message.sender,
                )
                return
            repository = _repository(core.store, environment)
            beta_export = (
                repository.export_user_status(message.sender)
                if repository is not None
                else {"status": "unavailable", "records": []}
            )
            reply = base_reply + "\n\n" + render_beta_export(language, beta_export)
            await core._finish(message.message_id, reply, message.sender)
            return

        if commands_allowed and is_leave_request(text):
            repository = _repository(core.store, environment)
            if repository is None:
                reply = beta_privacy_unavailable_message(language)
            else:
                exported = repository.export_user_status(message.sender)
                if exported.get("status") != "available":
                    reply = beta_privacy_unavailable_message(language)
                elif bool(exported.get("active")):
                    reply = (
                        beta_left_message(language)
                        if repository.revoke(message.sender)
                        else beta_privacy_unavailable_message(language)
                    )
                else:
                    reply = beta_not_active_message(language)
            await core._finish(message.message_id, reply, message.sender)
            return

        outcome = evaluate_beta_admission(
            store=core.store,
            phone=message.sender,
            text=text if commands_allowed else "",
            language=language,
            env=environment,
        )
        if outcome.should_continue:
            await original(message)
            return
        await core._finish(message.message_id, outcome.reply, message.sender)

    core.process_incoming = process_incoming
    setattr(core, _CORE_MARKER, True)
