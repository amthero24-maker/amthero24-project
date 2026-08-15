"""Deterministic post-generation grounding for selected official-draft journeys.

This module is read-only. It does not call providers, persist data, send messages,
or execute refunds, appointment changes, or contract actions. It validates that
copy-ready drafts remain bounded by user-supplied facts.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from journey_grounding_patterns import (
    JOURNEY_APPOINTMENT,
    JOURNEY_CONTRACT,
    JOURNEY_REFUND,
    _ADDRESS_PATTERN,
    _AMOUNT_PATTERN,
    _APPOINTMENT_ACTION_PATTERNS,
    _APPOINTMENT_PATTERNS,
    _APPOINTMENT_UNSUPPORTED,
    _CANCELLATION_ACTION_PATTERNS,
    _CANCELLATION_PATTERNS,
    _COMPANY_PATTERN,
    _CONTRACT_CLARIFICATION_PATTERN,
    _CONTRACT_FOLLOWUP_PATTERNS,
    _CONTRACT_PATTERNS,
    _CONTRACT_UNCERTAINTY_PATTERNS,
    _CONTRACT_UNSUPPORTED,
    _DATE_PATTERN,
    _DURATION_PATTERN,
    _FACT_LINE_PATTERN,
    _IDENTIFIER_PATTERN,
    _LABELLED_VALUE_PATTERN,
    _LOCATION_LABEL_MARKERS,
    _NEGATION_PATTERN,
    _REFUND_PATTERNS,
    _REFUND_PROBLEM_PATTERNS,
    _REFUND_UNSUPPORTED,
    _REVISION_CHANGE_PATTERN,
    _REVISION_TYPE_PATTERNS,
    _SUPPORTED_LANGUAGES,
    _TIME_PATTERN,
)


@dataclass(frozen=True)
class JourneyDraftGroundingResult:
    applicable: bool
    journey: str
    draft: str
    changed: bool = False
    rejection_reason: str = ""


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in text
        if ch in {"\n", "\t"} or unicodedata.category(ch) not in {"Cf", "Cs"}
    ).strip()


def _fold(value: str) -> str:
    return re.sub(r"\s+", " ", _normalize(value)).strip().casefold()


def _selected_language(value: str) -> str:
    return value if value in _SUPPORTED_LANGUAGES else "de"


def _contains_any(value: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    text = _normalize(value)
    return any(pattern.search(text) for pattern in patterns)


def _looks_like_official_draft(value: str) -> bool:
    text = _normalize(value)
    folded = text.casefold()
    subject = any(marker in folded for marker in ("betreff:", "subject:", "الموضوع:", "тема:", "θέμα:", "θεμα:"))
    salutation = any(marker.casefold() in folded for marker in (
        "Sehr geehrte", "Guten Tag", "Dear ", "السادة", "السيد", "السيدة", "Шановн", "Αξιότιμ",
    ))
    closing = any(marker.casefold() in folded for marker in (
        "Mit freundlichen Grüßen", "Mit freundlichen Gruessen", "Kind regards", "Sincerely",
        "مع خالص التحية", "مع الاحترام", "З повагою", "Με εκτίμηση", "Με εκτιμηση",
    ))
    return closing and (subject or salutation)


def classify_journey_request(value: str) -> str | None:
    text = _normalize(value)
    if not text:
        return None
    if _contains_any(text, _REFUND_PATTERNS):
        return JOURNEY_REFUND
    if _contains_any(text, _APPOINTMENT_PATTERNS):
        return JOURNEY_APPOINTMENT
    contract_present = _contains_any(text, _CONTRACT_PATTERNS)
    if contract_present and _contains_any(text, _CONTRACT_FOLLOWUP_PATTERNS):
        return JOURNEY_CONTRACT
    if _contains_any(text, _CANCELLATION_PATTERNS):
        return None
    if contract_present:
        return JOURNEY_CONTRACT
    return None


def classify_journey_draft(value: str) -> str | None:
    text = _normalize(value)
    if not _looks_like_official_draft(text):
        return None
    if _contains_any(text, _REFUND_PATTERNS):
        return JOURNEY_REFUND
    if _contains_any(text, _APPOINTMENT_PATTERNS):
        return JOURNEY_APPOINTMENT
    if _contains_any(text, _CANCELLATION_ACTION_PATTERNS):
        return None
    if _contains_any(text, _CONTRACT_PATTERNS):
        return JOURNEY_CONTRACT
    return None


def classify_journey(request_text: str, previous_draft: str = "") -> str | None:
    return classify_journey_request(request_text) or classify_journey_draft(previous_draft)


def _instruction_prefix(value: str) -> str:
    text = _normalize(value)
    return re.split(r"\n\s*\n", text, maxsplit=1)[0][:1200].strip()


def _labelled_lines(value: str) -> str:
    text = _normalize(value)
    return "\n".join(match.group(0) for match in _FACT_LINE_PATTERN.finditer(text))


def _is_negated(text: str, start: int) -> bool:
    boundary = max(
        text.rfind("\n", 0, start),
        text.rfind(".", 0, start),
        text.rfind("?", 0, start),
        text.rfind("!", 0, start),
        text.rfind("؛", 0, start),
    )
    prefix = text[max(boundary + 1, start - 36):start]
    return bool(_NEGATION_PATTERN.search(prefix))


def _canonical_date(value: str) -> str:
    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", raw):
        year, month, day = raw.split("-")
    else:
        parts = re.split(r"[./-]", raw)
        if len(parts) != 3:
            return _fold(raw)
        day, month, year = parts
        if len(year) == 2:
            year = "20" + year
    try:
        return f"date:{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return "date:" + _fold(raw)


def _canonical_time(value: str) -> str:
    raw = re.sub(r"\s*Uhr\s*$", "", value, flags=re.IGNORECASE).replace(".", ":")
    hour, minute = raw.split(":", 1)
    return f"time:{int(hour):02d}:{int(minute):02d}"


def _canonical_amount(value: str) -> str:
    match = re.search(r"(\d{1,9}(?:[.,]\d{1,2})?)\s*(€|EUR|USD|CHF|GBP)", value, re.IGNORECASE)
    if not match:
        return "amount:" + _fold(value)
    number = match.group(1).replace(",", ".")
    currency = "EUR" if match.group(2) == "€" else match.group(2).upper()
    try:
        normalized = f"{float(number):.2f}"
    except ValueError:
        normalized = number
    return f"amount:{normalized}:{currency}"


def _canonical_duration(value: str) -> str:
    return "duration:" + _fold(value)


def _canonical_identifier(value: str) -> str:
    return "id:" + _fold(value)


def _canonical_named(value: str) -> str:
    return "name:" + _fold(value).strip(" .,:;-")


def _canonical_address(value: str) -> str:
    return "address:" + _fold(value).strip(" .,:;-")


def _extract_anchors(value: str) -> dict[str, str]:
    text = _normalize(value)
    anchors: dict[str, str] = {}

    def add(pattern: re.Pattern[str], canonicalizer) -> None:
        for match in pattern.finditer(text):
            if _is_negated(text, match.start()):
                continue
            key = canonicalizer(match.group(0))
            anchors.setdefault(key, match.group(0).strip())

    add(_DATE_PATTERN, _canonical_date)
    add(_TIME_PATTERN, _canonical_time)
    add(_AMOUNT_PATTERN, _canonical_amount)
    add(_DURATION_PATTERN, _canonical_duration)
    add(_COMPANY_PATTERN, _canonical_named)
    add(_ADDRESS_PATTERN, _canonical_address)
    add(_IDENTIFIER_PATTERN, _canonical_identifier)

    for match in _LABELLED_VALUE_PATTERN.finditer(text):
        label = _fold(match.group(1))
        value_text = match.group(2).strip().strip(" .,:;-")
        if not value_text or _is_negated(text, match.start(2)):
            continue
        canonicalizer = (
            _canonical_address
            if any(marker in label for marker in _LOCATION_LABEL_MARKERS)
            else _canonical_named
        )
        anchors.setdefault(canonicalizer(value_text), value_text)
    return anchors


def _allowed_source_text(request_text: str, previous_draft: str) -> str:
    return "\n".join(part for part in (_normalize(request_text), _normalize(previous_draft)) if part)


def _anchor_type(key: str) -> str:
    return key.split(":", 1)[0]


def _relaxed_revision_types(request_text: str) -> frozenset[str]:
    prefix = _instruction_prefix(request_text)
    if not _REVISION_CHANGE_PATTERN.search(prefix):
        return frozenset()
    return frozenset(
        anchor_type
        for anchor_type, pattern in _REVISION_TYPE_PATTERNS.items()
        if pattern.search(prefix)
    )


def _source_anchor_sets(
    request_text: str,
    previous_draft: str,
) -> tuple[dict[str, str], dict[str, str]]:
    relaxed = _relaxed_revision_types(request_text)
    current_allowed = _extract_anchors(_normalize(request_text))
    previous_allowed = (
        _extract_anchors(_normalize(previous_draft))
        if classify_journey_draft(previous_draft)
        else {}
    )
    allowed = dict(current_allowed)
    allowed.update(
        {
            key: value
            for key, value in previous_allowed.items()
            if _anchor_type(key) not in relaxed
        }
    )

    current_required = _extract_anchors(
        "\n".join(
            part for part in (
                _instruction_prefix(request_text),
                _labelled_lines(request_text),
            )
            if part
        )
    )
    previous_required = (
        _extract_anchors(_normalize(previous_draft))
        if classify_journey_draft(previous_draft)
        else {}
    )
    required = dict(current_required)
    required.update(
        {
            key: value
            for key, value in previous_required.items()
            if _anchor_type(key) not in relaxed
        }
    )
    return allowed, required


def _question_like(text: str, start: int, end: int) -> bool:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), text.rfind("?", 0, start)) + 1
    right_candidates = [pos for pos in (text.find(".", end), text.find("\n", end), text.find("?", end)) if pos >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    sentence = text[left:right + 1]
    folded = sentence.casefold()
    return (
        "?" in sentence
        or bool(re.search(r"\b(?:ob|whether|if)\b", folded))
        or any(marker in folded for marker in ("يرجى توضيح ما إذا", "هل ", "чи ", "αν "))
    )


def _matched_semantic_categories(
    value: str,
    groups: dict[str, tuple[re.Pattern[str], ...]],
) -> frozenset[str]:
    text = _normalize(value)
    return frozenset(
        category
        for category, patterns in groups.items()
        if any(pattern.search(text) for pattern in patterns)
    )


def _appointment_action_key(value: str) -> str:
    text = _normalize(value)
    for action in ("reschedule", "cancel", "confirm"):
        if any(pattern.search(text) for pattern in _APPOINTMENT_ACTION_PATTERNS[action]):
            return action
    return ""


def _semantic_grounding_reason(journey: str, source: str, draft: str) -> str:
    if journey == JOURNEY_REFUND:
        source_problems = _matched_semantic_categories(source, _REFUND_PROBLEM_PATTERNS)
        draft_problems = _matched_semantic_categories(draft, _REFUND_PROBLEM_PATTERNS)
        if draft_problems - source_problems:
            return "unsupported-problem-added"
        if source_problems - draft_problems:
            return "verified-problem-missing"
        return ""

    if journey == JOURNEY_APPOINTMENT:
        source_action = _appointment_action_key(source)
        draft_action = _appointment_action_key(draft)
        if source_action and draft_action != source_action:
            return "appointment-action-mismatch"
        return ""

    if journey == JOURNEY_CONTRACT:
        uncertainty_supplied = any(
            pattern.search(_normalize(source))
            for pattern in _CONTRACT_UNCERTAINTY_PATTERNS
        )
        if uncertainty_supplied and not _CONTRACT_CLARIFICATION_PATTERN.search(_normalize(draft)):
            return "contract-uncertainty-not-visible"
    return ""


def _introduced_claim(journey: str, draft: str, source: str) -> str:
    groups = {
        JOURNEY_REFUND: _REFUND_UNSUPPORTED,
        JOURNEY_APPOINTMENT: _APPOINTMENT_UNSUPPORTED,
        JOURNEY_CONTRACT: _CONTRACT_UNSUPPORTED,
    }.get(journey, ())
    always_reject = {
        "refund-guarantee",
        "refund-legal-entitlement",
        "refund-threat",
        "refund-deadline",
        "contract-validity",
        "contract-legal-entitlement",
    }
    for reason, pattern in groups:
        source_contains = bool(pattern.search(source))
        for match in pattern.finditer(draft):
            if journey == JOURNEY_CONTRACT and reason == "contract-validity" and _question_like(draft, match.start(), match.end()):
                continue
            if reason not in always_reject and source_contains:
                continue
            return reason
    return ""


def _draft_header_names(value: str) -> dict[str, str]:
    text = _normalize(value)
    names: dict[str, str] = {}
    for line in text.splitlines()[:8]:
        candidate = line.strip().strip(" .,:;-")
        if not candidate:
            continue
        folded = candidate.casefold()
        if folded.startswith(("betreff:", "subject:", "الموضوع:", "тема:", "θέμα:", "θεμα:")):
            break
        if candidate.startswith("[") and candidate.endswith("]"):
            continue
        if re.search(r"\d{4,}", candidate):
            continue
        if any(marker in folded for marker in ("straße", "strasse", "postleitzahl", "adresse", "address")):
            continue
        if len(candidate) > 100:
            continue
        names.setdefault(_canonical_named(candidate), candidate)
        break
    return names


def ground_journey_draft(
    request_text: str,
    draft: str,
    *,
    previous_draft: str = "",
    conversation_language: str = "de",
) -> JourneyDraftGroundingResult:
    clean = _normalize(draft)
    journey = classify_journey(request_text, previous_draft)
    if journey is None:
        return JourneyDraftGroundingResult(applicable=False, journey="", draft=clean)
    if classify_journey_draft(clean) != journey:
        return JourneyDraftGroundingResult(applicable=False, journey=journey, draft=clean)

    allowed, required = _source_anchor_sets(request_text, previous_draft)
    output = _extract_anchors(clean)
    output.update(_draft_header_names(clean))

    extra = sorted(set(output) - set(allowed))
    if extra:
        kind = extra[0].split(":", 1)[0]
        return JourneyDraftGroundingResult(
            applicable=True,
            journey=journey,
            draft=clean,
            rejection_reason=f"unsupported-{kind}-added",
        )

    missing: list[str] = []
    folded_draft = _fold(clean)
    for key, display in required.items():
        if key.startswith(("name:", "address:")):
            if _fold(display) not in folded_draft:
                missing.append(key)
        elif key not in output:
            missing.append(key)
    if missing:
        return JourneyDraftGroundingResult(
            applicable=True,
            journey=journey,
            draft=clean,
            rejection_reason="verified-anchor-missing",
        )

    source_context = _allowed_source_text(request_text, previous_draft)
    claim_reason = _introduced_claim(journey, clean, source_context)
    if claim_reason:
        return JourneyDraftGroundingResult(
            applicable=True,
            journey=journey,
            draft=clean,
            rejection_reason=claim_reason,
        )

    semantic_reason = _semantic_grounding_reason(journey, source_context, clean)
    if semantic_reason:
        return JourneyDraftGroundingResult(
            applicable=True,
            journey=journey,
            draft=clean,
            rejection_reason=semantic_reason,
        )

    return JourneyDraftGroundingResult(
        applicable=True,
        journey=journey,
        draft=clean,
        changed=False,
    )
