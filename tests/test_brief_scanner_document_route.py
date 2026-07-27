from __future__ import annotations

from datetime import date

from brief_scanner_contract import BriefScannerFacts
from brief_scanner_document_route import handle_brief_scanner_document
from brief_scanner_model_boundary import BriefScannerBoundaryOutcome, BriefScannerBoundaryStatus


def _outcome(
    status: BriefScannerBoundaryStatus,
    *,
    language: str = "de",
    error_code: str = "",
    amount_minor: int = 12550,
    currency: str = "EUR",
) -> BriefScannerBoundaryOutcome:
    facts = None
    if status in {
        BriefScannerBoundaryStatus.VALIDATED,
        BriefScannerBoundaryStatus.VALIDATED_READ_ONLY,
        BriefScannerBoundaryStatus.BLOCKED_OR_ESCALATED,
        BriefScannerBoundaryStatus.RETRYABLE_DOCUMENT_QUALITY,
    }:
        facts = BriefScannerFacts(
            language=language,
            readable=status != BriefScannerBoundaryStatus.RETRYABLE_DOCUMENT_QUALITY,
            uncertainty="image_quality_low" if status == BriefScannerBoundaryStatus.RETRYABLE_DOCUMENT_QUALITY else "",
            sender_organization="Synthetic Authority",
            deadline=date(2026, 8, 15),
            requested_action="send documents",
            amount_minor=amount_minor,
            currency=currency,
            reference_number="SYNTHETIC-REF-001",
        )
    return BriefScannerBoundaryOutcome(status=status, facts=facts, error_code=error_code)


def test_disabled_provider_falls_back_to_existing_route() -> None:
    calls = 0

    def provider(**_kwargs):
        nonlocal calls
        calls += 1
        return _outcome(
            BriefScannerBoundaryStatus.RETRYABLE_MODEL_OUTPUT,
            error_code="brief_scanner_provider_disabled",
        )

    result = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="ar",
        provider=provider,
    )

    assert calls == 1
    assert result.handled is False
    assert result.reply == ""
    assert result.allows_side_effects is False


def test_non_image_document_is_not_intercepted_or_sent_to_provider() -> None:
    called = False

    def provider(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    result = handle_brief_scanner_document(
        image_bytes=b"pdf",
        mime_type="application/pdf",
        response_language="de",
        provider=provider,
        enabled=True,
    )

    assert result.handled is False
    assert called is False


def test_unknown_reply_language_falls_back_without_provider_or_language_mixing() -> None:
    called = False

    def provider(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    result = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="fr",
        provider=provider,
        enabled=True,
    )

    assert result.handled is False
    assert result.reply == ""
    assert called is False


def test_validated_result_returns_bounded_read_only_summary() -> None:
    result = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/png",
        response_language="ar",
        provider=lambda **_kwargs: _outcome(BriefScannerBoundaryStatus.VALIDATED),
        enabled=True,
    )

    assert result.handled is True
    assert "Synthetic Authority" in result.reply
    assert "2026-08-15" in result.reply
    assert "125.50 EUR" in result.reply
    assert "SYNTHETIC-REF-001" in result.reply
    assert result.allows_side_effects is False


def test_currency_minor_units_respect_zero_and_three_decimal_exponents() -> None:
    jpy = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="en",
        provider=lambda **_kwargs: _outcome(
            BriefScannerBoundaryStatus.VALIDATED,
            amount_minor=12550,
            currency="JPY",
        ),
        enabled=True,
    )
    kwd = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="en",
        provider=lambda **_kwargs: _outcome(
            BriefScannerBoundaryStatus.VALIDATED,
            amount_minor=12550,
            currency="KWD",
        ),
        enabled=True,
    )

    assert "12550 JPY" in jpy.reply
    assert "12.550 KWD" in kwd.reply


def test_unverified_language_remains_read_only_and_is_explicit() -> None:
    result = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/webp",
        response_language="en",
        provider=lambda **_kwargs: _outcome(
            BriefScannerBoundaryStatus.VALIDATED_READ_ONLY,
            language="fr",
        ),
        enabled=True,
    )

    assert result.handled is True
    assert "read-only" in result.reply
    assert "no task or reminder" in result.reply
    assert result.allows_side_effects is False


def test_ukrainian_and_greek_replies_do_not_fall_back_to_german() -> None:
    ukrainian = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="uk",
        provider=lambda **_kwargs: _outcome(BriefScannerBoundaryStatus.RETRYABLE_MODEL_OUTPUT),
        enabled=True,
    )
    greek = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="el",
        provider=lambda **_kwargs: _outcome(BriefScannerBoundaryStatus.RETRYABLE_MODEL_OUTPUT),
        enabled=True,
    )

    assert "Зараз документ" in ukrainian.reply
    assert "Το έγγραφο" in greek.reply
    assert "Das Dokument" not in ukrainian.reply
    assert "Das Dokument" not in greek.reply


def test_quality_and_high_risk_outcomes_never_expose_document_fields() -> None:
    quality = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="de",
        provider=lambda **_kwargs: _outcome(BriefScannerBoundaryStatus.RETRYABLE_DOCUMENT_QUALITY),
        enabled=True,
    )
    escalated = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="ar",
        provider=lambda **_kwargs: _outcome(BriefScannerBoundaryStatus.BLOCKED_OR_ESCALATED),
        enabled=True,
    )

    for result in (quality, escalated):
        assert result.handled is True
        assert "Synthetic Authority" not in result.reply
        assert "SYNTHETIC-REF-001" not in result.reply
        assert result.allows_side_effects is False


def test_provider_failure_returns_safe_generic_message_without_error_code() -> None:
    result = handle_brief_scanner_document(
        image_bytes=b"image",
        mime_type="image/jpeg",
        response_language="en",
        provider=lambda **_kwargs: _outcome(
            BriefScannerBoundaryStatus.RETRYABLE_MODEL_OUTPUT,
            error_code="brief_scanner_provider_request_failed",
        ),
        enabled=True,
    )

    assert result.handled is True
    assert "could not be analyzed safely" in result.reply
    assert "brief_scanner_provider_request_failed" not in result.reply
    assert result.allows_side_effects is False
