"""Disabled-by-default Groq provider for bounded Brief Scanner draft translation.

This provider does not persist or deliver drafts and is not wired into the runtime adapter.
It returns only a strict boundary outcome and logs no request or provider exception content.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from groq import Groq

from brief_scanner_draft_boundary import (
    BriefScannerDraftBoundaryOutcome,
    BriefScannerDraftBoundaryStatus,
    build_brief_scanner_draft_prompt,
    evaluate_brief_scanner_draft_output,
)
from brief_scanner_execution_boundary import BriefScannerDraftCommand
from config import GROQ_MODEL, required_env

logger = logging.getLogger(__name__)


def _retryable(code: str) -> BriefScannerDraftBoundaryOutcome:
    return BriefScannerDraftBoundaryOutcome(
        BriefScannerDraftBoundaryStatus.RETRYABLE_MODEL_OUTPUT,
        error_code=code,
    )


def generate_brief_scanner_draft_with_groq(
    command: BriefScannerDraftCommand,
    *,
    client_factory: Callable[..., Any] = Groq,
    enabled: bool = False,
) -> BriefScannerDraftBoundaryOutcome:
    """Call Groq once only after explicit enablement and validate its JSON response."""
    if type(enabled) is not bool:
        return _retryable("brief_scanner_draft_provider_flag_invalid")
    if not enabled:
        return _retryable("brief_scanner_draft_provider_disabled")
    try:
        prompt = build_brief_scanner_draft_prompt(command)
    except ValueError as exc:
        return _retryable(str(exc))

    request: dict[str, object] = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "Translate the approved response instruction and return the required JSON "
                    "object only."
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 600,
    }
    if GROQ_MODEL.startswith("qwen/"):
        request["reasoning_format"] = "hidden"
        request["reasoning_effort"] = "none"
    elif GROQ_MODEL.startswith("openai/gpt-oss-"):
        request["include_reasoning"] = False
        request["reasoning_effort"] = "low"

    try:
        client = client_factory(api_key=required_env("GROQ_API_KEY"))
        completion = client.chat.completions.create(**request)
        raw_output = completion.choices[0].message.content
        if type(raw_output) is not str:
            return _retryable("brief_scanner_draft_provider_output_invalid")
        return evaluate_brief_scanner_draft_output(raw_output)
    except Exception:
        logger.error("Brief Scanner draft provider request failed")
        return _retryable("brief_scanner_draft_provider_request_failed")
