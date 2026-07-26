"""Strict adapter from untrusted model JSON to the Brief Scanner contract.

The adapter accepts one bounded JSON object only. It rejects markdown wrappers, unknown keys,
duplicate keys, coerced scalar types, malformed dates, oversized text, and structurally
incomplete outcomes. No persistence, telemetry, mission mutation, or model call occurs here.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Final

from brief_scanner_contract import BriefScannerFacts

MAX_MODEL_OUTPUT_BYTES: Final[int] = 16_384
MAX_TEXT_LENGTH: Final[int] = 500
SCHEMA_VERSION: Final[int] = 1

_REQUIRED_FIELDS: Final[frozenset[str]] = frozenset({"schema_version", "language", "readable"})
_OPTIONAL_FIELDS: Final[frozenset[str]] = frozenset({
    "missing_pages",
    "sender_organization",
    "document_date",
    "deadline",
    "appointment_date",
    "requested_action",
    "amount_minor",
    "currency",
    "stated_consequence",
    "contact_channel",
    "reference_number",
    "risk_category",
    "uncertainty",
})
_ALLOWED_FIELDS: Final[frozenset[str]] = _REQUIRED_FIELDS | _OPTIONAL_FIELDS
_DATE_FIELDS: Final[tuple[str, ...]] = ("document_date", "deadline", "appointment_date")
_TEXT_FIELDS: Final[tuple[str, ...]] = (
    "sender_organization",
    "requested_action",
    "currency",
    "stated_consequence",
    "contact_channel",
    "reference_number",
    "risk_category",
    "uncertainty",
)


class BriefScannerAdapterError(ValueError):
    """Sanitized parsing failure safe for operational handling."""


def _fail(code: str) -> BriefScannerAdapterError:
    return BriefScannerAdapterError(code)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate keys at any depth."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail(f"brief_scanner_field_duplicate:{key}")
        result[key] = value
    return result


def _strict_bool(payload: dict[str, Any], field: str, *, default: bool | None = None) -> bool:
    if field not in payload:
        if default is None:
            raise _fail(f"brief_scanner_field_missing:{field}")
        return default
    value = payload[field]
    if type(value) is not bool:
        raise _fail(f"brief_scanner_field_type_invalid:{field}")
    return value


def _bounded_text(payload: dict[str, Any], field: str, *, default: str = "") -> str:
    value = payload.get(field, default)
    if type(value) is not str:
        raise _fail(f"brief_scanner_field_type_invalid:{field}")
    cleaned = value.strip()
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise _fail(f"brief_scanner_field_too_long:{field}")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in cleaned):
        raise _fail(f"brief_scanner_field_control_character:{field}")
    return cleaned


def _optional_date(payload: dict[str, Any], field: str) -> date | None:
    value = payload.get(field)
    if value is None:
        return None
    if type(value) is not str:
        raise _fail(f"brief_scanner_field_type_invalid:{field}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _fail(f"brief_scanner_date_invalid:{field}") from exc
    if parsed.isoformat() != value:
        raise _fail(f"brief_scanner_date_invalid:{field}")
    return parsed


def _optional_amount(payload: dict[str, Any]) -> int | None:
    value = payload.get("amount_minor")
    if value is None:
        return None
    if type(value) is not int:
        raise _fail("brief_scanner_field_type_invalid:amount_minor")
    if value < 0:
        raise _fail("brief_scanner_amount_invalid")
    return value


def parse_brief_scanner_model_output(raw_output: str) -> BriefScannerFacts:
    """Parse exactly one strict JSON object into validated contract facts."""
    if type(raw_output) is not str:
        raise _fail("brief_scanner_output_type_invalid")
    encoded = raw_output.encode("utf-8")
    if not encoded or len(encoded) > MAX_MODEL_OUTPUT_BYTES:
        raise _fail("brief_scanner_output_size_invalid")
    try:
        payload = json.loads(raw_output, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise _fail("brief_scanner_json_invalid") from exc
    if type(payload) is not dict:
        raise _fail("brief_scanner_root_invalid")

    keys = frozenset(payload)
    missing = sorted(_REQUIRED_FIELDS - keys)
    if missing:
        raise _fail(f"brief_scanner_field_missing:{missing[0]}")
    unknown = sorted(keys - _ALLOWED_FIELDS)
    if unknown:
        raise _fail(f"brief_scanner_field_unknown:{unknown[0]}")

    version = payload["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise _fail("brief_scanner_schema_version_invalid")

    language = _bounded_text(payload, "language")
    readable = _strict_bool(payload, "readable")
    missing_pages = _strict_bool(payload, "missing_pages", default=False)
    values = {field: _bounded_text(payload, field) for field in _TEXT_FIELDS}
    dates = {field: _optional_date(payload, field) for field in _DATE_FIELDS}

    facts = BriefScannerFacts(
        language=language,
        readable=readable,
        missing_pages=missing_pages,
        sender_organization=values["sender_organization"],
        document_date=dates["document_date"],
        deadline=dates["deadline"],
        appointment_date=dates["appointment_date"],
        requested_action=values["requested_action"],
        amount_minor=_optional_amount(payload),
        currency=values["currency"] or "EUR",
        stated_consequence=values["stated_consequence"],
        contact_channel=values["contact_channel"],
        reference_number=values["reference_number"],
        risk_category=values["risk_category"],
        uncertainty=values["uncertainty"],
    )
    try:
        facts.validate()
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    return facts
