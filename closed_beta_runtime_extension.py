"""Final runtime wrapper for Closed Beta admission.

Installed after reminder/Sam composition. With admission disabled (the production
default) it delegates directly to the previously composed process_incoming path.
"""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from closed_beta_admission_service import evaluate_beta_admission

_INSTALLED = False


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


def install(core: Any) -> None:
    """Install one idempotent wrapper around the already-composed app path."""
    global _INSTALLED
    if _INSTALLED:
        return

    original: Callable[[Any], Awaitable[None]] = core.process_incoming

    async def process_incoming(message: Any) -> None:
        language = _language(core, message)
        outcome = evaluate_beta_admission(
            store=core.store,
            phone=message.sender,
            text=str(message.text or ""),
            language=language,
            env=os.environ,
        )
        if outcome.should_continue:
            await original(message)
            return
        await core._finish(message.message_id, outcome.reply, message.sender)

    core.process_incoming = process_incoming
    _INSTALLED = True
