"""Fail-safe application hook for read-only Brief Scanner routing.

This module keeps the main webhook wiring small and testable. It never persists data or creates
missions, reminders, drafts, or telemetry. A handled reply is returned only when the read-only
route explicitly accepts the media; otherwise callers continue through the existing model path.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from brief_scanner_document_route import BriefScannerRouteResult, handle_brief_scanner_document


@dataclass(frozen=True)
class BriefScannerAppDecision:
    use_existing_flow: bool
    reply: str = ""

    @property
    def allows_side_effects(self) -> bool:
        return False


RouteCall = Callable[..., BriefScannerRouteResult]


def decide_brief_scanner_media_flow(
    *,
    image_bytes: bytes | None,
    mime_type: str,
    response_language: str,
    route: RouteCall = handle_brief_scanner_document,
    enabled: bool | None = None,
) -> BriefScannerAppDecision:
    """Return either a bounded read-only reply or an instruction to preserve the existing flow."""
    if image_bytes is None:
        return BriefScannerAppDecision(use_existing_flow=True)

    result = route(
        image_bytes=image_bytes,
        mime_type=mime_type,
        response_language=response_language,
        enabled=enabled,
    )
    if not result.handled:
        return BriefScannerAppDecision(use_existing_flow=True)
    if type(result.reply) is not str or not result.reply.strip():
        return BriefScannerAppDecision(use_existing_flow=True)
    return BriefScannerAppDecision(use_existing_flow=False, reply=result.reply.strip())
