from __future__ import annotations

from brief_scanner_app_hook import decide_brief_scanner_media_flow
from brief_scanner_document_route import BriefScannerRouteResult


def test_missing_media_preserves_existing_flow_without_route_call() -> None:
    called = False

    def route(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("route must not be called")

    decision = decide_brief_scanner_media_flow(
        image_bytes=None,
        mime_type="image/jpeg",
        response_language="ar",
        route=route,
        enabled=True,
    )

    assert decision.use_existing_flow is True
    assert decision.reply == ""
    assert decision.allows_side_effects is False
    assert called is False


def test_unhandled_route_preserves_existing_flow() -> None:
    decision = decide_brief_scanner_media_flow(
        image_bytes=b"image",
        mime_type="application/pdf",
        response_language="de",
        route=lambda **_kwargs: BriefScannerRouteResult(handled=False),
        enabled=True,
    )

    assert decision.use_existing_flow is True
    assert decision.allows_side_effects is False


def test_handled_route_returns_read_only_reply() -> None:
    decision = decide_brief_scanner_media_flow(
        image_bytes=b"image",
        mime_type="image/png",
        response_language="en",
        route=lambda **_kwargs: BriefScannerRouteResult(
            handled=True,
            reply="  Document overview:\nDeadline: 2026-08-15  ",
        ),
        enabled=True,
    )

    assert decision.use_existing_flow is False
    assert decision.reply == "Document overview:\nDeadline: 2026-08-15"
    assert decision.allows_side_effects is False


def test_empty_handled_reply_fails_back_to_existing_flow() -> None:
    decision = decide_brief_scanner_media_flow(
        image_bytes=b"image",
        mime_type="image/webp",
        response_language="uk",
        route=lambda **_kwargs: BriefScannerRouteResult(handled=True, reply="   "),
        enabled=True,
    )

    assert decision.use_existing_flow is True
    assert decision.reply == ""
    assert decision.allows_side_effects is False
