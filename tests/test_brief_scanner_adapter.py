from __future__ import annotations

import json
from datetime import date

import pytest

from brief_scanner_adapter import BriefScannerAdapterError, parse_brief_scanner_model_output


def _payload(**overrides):
    payload = {
        "schema_version": 1,
        "language": "de",
        "readable": True,
        "missing_pages": False,
        "sender_organization": "Synthetic Authority",
        "document_date": "2026-07-20",
        "deadline": "2026-08-15",
        "appointment_date": None,
        "requested_action": "send documents",
        "amount_minor": 12550,
        "currency": "EUR",
        "stated_consequence": "synthetic consequence",
        "contact_channel": "postal reply",
        "reference_number": "SYNTHETIC-REF-001",
        "risk_category": "",
        "uncertainty": "",
    }
    payload.update(overrides)
    return payload


def test_valid_json_maps_to_contract_without_coercion() -> None:
    facts = parse_brief_scanner_model_output(json.dumps(_payload()))

    assert facts.language == "de"
    assert facts.readable is True
    assert facts.deadline == date(2026, 8, 15)
    assert facts.amount_minor == 12550
    assert facts.currency == "EUR"


@pytest.mark.parametrize(
    "raw,code",
    [
        ("```json\n{}\n```", "brief_scanner_json_invalid"),
        ("[]", "brief_scanner_root_invalid"),
        (json.dumps({"schema_version": 1, "language": "de"}), "brief_scanner_field_missing:readable"),
        (json.dumps(_payload(extra_field="unexpected")), "brief_scanner_field_unknown:extra_field"),
        (json.dumps(_payload(schema_version=2)), "brief_scanner_schema_version_invalid"),
        (json.dumps(_payload(readable="true")), "brief_scanner_field_type_invalid:readable"),
        (json.dumps(_payload(missing_pages=1)), "brief_scanner_field_type_invalid:missing_pages"),
        (json.dumps(_payload(amount_minor=True)), "brief_scanner_field_type_invalid:amount_minor"),
        (json.dumps(_payload(deadline="15.08.2026")), "brief_scanner_date_invalid:deadline"),
    ],
)
def test_invalid_model_outputs_fail_closed_with_sanitized_codes(raw: str, code: str) -> None:
    with pytest.raises(BriefScannerAdapterError) as raised:
        parse_brief_scanner_model_output(raw)

    assert str(raised.value) == code
    assert "Synthetic Authority" not in str(raised.value)
    assert "SYNTHETIC-REF-001" not in str(raised.value)


def test_unreadable_output_requires_safe_uncertainty_reason() -> None:
    with pytest.raises(BriefScannerAdapterError, match="unreadable_document_requires_reason"):
        parse_brief_scanner_model_output(json.dumps(_payload(readable=False, uncertainty="")))

    facts = parse_brief_scanner_model_output(
        json.dumps(_payload(readable=False, uncertainty="image_quality_low"))
    )
    assert facts.readable is False
    assert facts.uncertainty == "image_quality_low"


def test_unsupported_language_and_invalid_currency_fail_closed() -> None:
    with pytest.raises(BriefScannerAdapterError, match="unsupported_brief_scanner_language"):
        parse_brief_scanner_model_output(json.dumps(_payload(language="fr")))
    with pytest.raises(BriefScannerAdapterError, match="brief_scanner_currency_invalid"):
        parse_brief_scanner_model_output(json.dumps(_payload(currency="EURO")))


def test_output_and_field_size_are_bounded() -> None:
    with pytest.raises(BriefScannerAdapterError, match="brief_scanner_field_too_long:requested_action"):
        parse_brief_scanner_model_output(json.dumps(_payload(requested_action="x" * 501)))
    with pytest.raises(BriefScannerAdapterError, match="brief_scanner_output_size_invalid"):
        parse_brief_scanner_model_output(" " * 16_385)


def test_control_characters_are_rejected() -> None:
    raw = json.dumps(_payload(reference_number="safe\u0000unsafe"))
    with pytest.raises(BriefScannerAdapterError, match="brief_scanner_field_control_character:reference_number"):
        parse_brief_scanner_model_output(raw)


def test_null_optional_dates_and_default_currency_are_supported() -> None:
    facts = parse_brief_scanner_model_output(
        json.dumps(_payload(document_date=None, deadline=None, appointment_date=None, amount_minor=None, currency=""))
    )
    assert facts.document_date is None
    assert facts.deadline is None
    assert facts.appointment_date is None
    assert facts.amount_minor is None
    assert facts.currency == "EUR"
