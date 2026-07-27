"""Provider-only Groq call for Brief Scanner structured document extraction.

This module performs no persistence, mission, reminder, telemetry, or WhatsApp mutation. It returns
only a bounded BriefScannerBoundaryOutcome and fails closed when disabled, misconfigured, or when
the provider response is malformed.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from typing import Any, Final

from groq import Groq

from brief_scanner_model_boundary import (
    BriefScannerBoundaryOutcome,
    BriefScannerBoundaryStatus,
    build_brief_scanner_extraction_prompt,
    evaluate_brief_scanner_model_output,
)
from config import BRIEF_SCANNER_PROVIDER_ENABLED, GROQ_VISION_MODEL, MAX_MEDIA_BYTES, required_env

logger = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset({"image/jpeg", "image/png", "image/webp"})


class BriefScannerProviderError(RuntimeError):
    """Sanitized provider-boundary failure."""


def _retryable(code: str) -> BriefScannerBoundaryOutcome:
    return BriefScannerBoundaryOutcome(
        status=BriefScannerBoundaryStatus.RETRYABLE_MODEL_OUTPUT,
        error_code=code,
    )


def extract_brief_with_groq(
    *,
    image_bytes: bytes,
    mime_type: str,
    response_language: str,
    client_factory: Callable[..., Any] = Groq,
    enabled: bool | None = None,
) -> BriefScannerBoundaryOutcome:
    """Call Groq vision once and validate its JSON through the strict model boundary."""
    if enabled is not None and type(enabled) is not bool:
        return _retryable("brief_scanner_provider_flag_invalid")
    active = BRIEF_SCANNER_PROVIDER_ENABLED if enabled is None else enabled
    if not active:
        return _retryable("brief_scanner_provider_disabled")
    if type(image_bytes) is not bytes or not image_bytes or len(image_bytes) > MAX_MEDIA_BYTES:
        return _retryable("brief_scanner_media_size_invalid")
    normalized_mime = (mime_type or "").strip().casefold()
    if normalized_mime not in _ALLOWED_MIME_TYPES:
        return _retryable("brief_scanner_media_type_invalid")

    try:
        prompt = build_brief_scanner_extraction_prompt(response_language=response_language)
    except ValueError as exc:
        return _retryable(str(exc))

    encoded = base64.b64encode(image_bytes).decode("ascii")
    request: dict[str, object] = {
        "model": GROQ_VISION_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract the document facts using the required JSON schema only.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{normalized_mime};base64,{encoded}"},
                    },
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 900,
    }
    if GROQ_VISION_MODEL.startswith("qwen/"):
        request["reasoning_format"] = "hidden"
        request["reasoning_effort"] = "none"

    try:
        client = client_factory(api_key=required_env("GROQ_API_KEY"))
        completion = client.chat.completions.create(**request)
        raw_output = completion.choices[0].message.content
        if type(raw_output) is not str:
            return _retryable("brief_scanner_provider_output_invalid")
        return evaluate_brief_scanner_model_output(raw_output)
    except Exception:
        # Do not log provider exception text or stack traces: SDK errors may echo request metadata.
        logger.error("Brief Scanner provider request failed")
        return _retryable("brief_scanner_provider_request_failed")
