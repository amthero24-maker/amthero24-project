"""Shared copy-safe delivery contract for official draft journeys.

This module has no provider, persistence, WhatsApp, payment, or action-runtime access.
It only detects explicit official-draft turns, builds a strict internal model envelope,
and validates/splits one model reply into a copy-ready draft plus a separate localized
explanation.
"""
from __future__ import annotations

import re
import unicodedata
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Final

DRAFT_OUTPUT_KIND: Final[str] = "official_draft"
ORDINARY_OUTPUT_KIND: Final[str] = "ordinary"
DRAFT_MARKER: Final[str] = "<<<AMTHERO24_DRAFT>>>"
EXPLANATION_MARKER: Final[str] = "<<<AMTHERO24_EXPLANATION>>>"
END_MARKER: Final[str] = "<<<AMTHERO24_END>>>"
_SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})
_ACTIVE_DRAFT_TURN: ContextVar[bool] = ContextVar(
    "amthero24_official_draft_turn_active",
    default=False,
)

_DRAFT_REQUEST_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?:اكتب(?:لي)?|اكتبي|صيغ(?:لي)?|صغ|جهز(?:لي)?|حض(?:ّ?ر)(?:لي)?|اعمل(?:لي)?)"
        r"\s+(?:لي\s+)?(?:رسالة|ايميل|إيميل|رد|جواب|اعتراض|شكوى|طلب|إلغاء|الغاء|فسخ|مطالبة)"
        r"|(?:بدي|أريد|اريد)\s+(?:رسالة|ايميل|إيميل|رد|اعتراض|شكوى|إلغاء|الغاء|فسخ)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:schreib|formulier|entwirf|erstelle|verfass|antworte|kündig|kuendig|widersprich)\w*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:write|draft|compose|prepare|formulate|reply|cancel|terminate)\b"
        r"|\b(?:i\s+(?:need|want|would\s+like)|please)\b.{0,80}"
        r"\b(?:letter|email|message|reply|request|cancellation|termination|appeal|complaint|refund)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:напиши|сформулюй|підготуй|склади|відповідай|скасуй|розірви)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:γράψε|σύνταξε|ετοίμασε|απάντησε|ακύρωσε|κατάγγειλε)",
        re.IGNORECASE,
    ),
)

_REVISION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?:عد[ّلل]|غي[ّرر]|صح[ّحح]|اختصر|أضف|اضف|احذف|بد[ّلل]).{0,50}"
        r"(?:المسودة|الرسالة|النص|الإيميل|الايميل)"
        r"|(?:المسودة|الرسالة|النص|الإيميل|الايميل).{0,50}"
        r"(?:عد[ّلل]|غي[ّرر]|صح[ّحح]|اختصر|أضف|اضف|احذف|بد[ّلل])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:änder|aender|korrigier|überarbeit|ueberarbeit|kürz|kuerz|ergänz|ergaenz)\w*"
        r".{0,50}(?:entwurf|schreiben|brief|e-?mail|text)"
        r"|(?:entwurf|schreiben|brief|e-?mail|text).{0,50}"
        r"(?:änder|aender|korrigier|überarbeit|ueberarbeit|kürz|kuerz|ergänz|ergaenz)\w*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:change|correct|revise|rewrite|shorten|expand|add|remove).{0,50}"
        r"(?:draft|letter|email|message|text)"
        r"|(?:draft|letter|email|message|text).{0,50}"
        r"(?:change|correct|revise|rewrite|shorten|expand|add|remove)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:зміни|виправ|перепиши|скороти|додай|видали).{0,50}"
        r"(?:чернетк|лист|повідомлен|текст)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:άλλαξε|διόρθωσε|ξαναγράψε|σύντομευσε|πρόσθεσε|αφαίρεσε).{0,50}"
        r"(?:προσχέδι|επιστολ|email|μήνυμα|κείμενο)",
        re.IGNORECASE,
    ),
)

_LANGUAGE_ONLY_REVISIONS: Final[frozenset[str]] = frozenset({
    "بالعربي", "بالعربية", "بالألماني", "بالالماني", "بالإنجليزي", "بالانجليزي",
    "auf deutsch", "in english", "deutsch", "english", "українською", "στα ελληνικά",
})

_META_HEADING = re.compile(
    r"^(?:entwurf|draft|مسودة|чернетка|προσχέδιο)\b",
    re.IGNORECASE,
)
_EXPLANATION_HEADING = re.compile(
    r"(?im)^\s*[*_#]*\s*(?:"
    r"ما\s+يعنيه\s+النص|ماذا\s+يعني\s+النص|الشرح|شرح|الخطوة\s+التالية|"
    r"was\s+der\s+text\s+bedeutet|erklärung|erklaerung|nächster\s+schritt|naechster\s+schritt|hinweis|"
    r"what\s+this\s+means|explanation|next\s+step|note|"
    r"пояснення|наступний\s+крок|"
    r"επεξήγηση|επομενο\s+βήμα|επόμενο\s+βήμα"
    r")\s*:?\s*[*_#]*\s*$"
)


class CopySafeDraftFormatError(RuntimeError):
    """Raised when a draft-like reply cannot be separated without ambiguity."""


@dataclass(frozen=True)
class CopySafeDraftReply:
    draft: str
    explanation: str
    conversation_language: str


def activate_official_draft_turn() -> Token[bool]:
    """Activate the prompt/delivery contract in the current async context only."""
    return _ACTIVE_DRAFT_TURN.set(True)


def reset_official_draft_turn(token: Token[bool]) -> None:
    _ACTIVE_DRAFT_TURN.reset(token)


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"}
        or unicodedata.category(character) not in {"Cf", "Cs"}
    ).strip()


def _instruction_prefix(text: str) -> str:
    normalized = _normalize_text(text)
    return re.split(r"\n\s*\n", normalized, maxsplit=1)[0][:900].strip()


def _normalized_command(text: str) -> str:
    value = _normalize_text(text).casefold().strip()
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def is_official_draft_turn(text: str, profile: dict[str, Any] | None = None) -> bool:
    """Return true only for an explicit draft request or a bounded draft revision."""
    if _ACTIVE_DRAFT_TURN.get():
        return True
    context = profile or {}
    if context.get("_official_draft_delivery_active") is True:
        return True

    prefix = _instruction_prefix(text)
    if prefix and any(pattern.search(prefix) for pattern in _DRAFT_REQUEST_PATTERNS):
        return True

    previous_reply = _strip_meta_heading(
        str(context.get("session_last_reply") or context.get("last_assistant_reply") or "")
    )
    if not _looks_like_official_draft(previous_reply):
        return False
    command = _normalized_command(prefix)
    if command in _LANGUAGE_ONLY_REVISIONS:
        return True
    return any(pattern.search(prefix) for pattern in _REVISION_PATTERNS)


def build_copy_safe_prompt_contract(*, active: bool, reply_language: str) -> str:
    """Build the internal envelope contract only for an official-draft turn."""
    if not active:
        return ""
    return f"""
COPY-SAFE OFFICIAL DRAFT DELIVERY — ACTIVE
Return exactly this internal envelope and nothing outside it:
{DRAFT_MARKER}
[only the complete copy-ready official draft; no markdown fence, no `Entwurf`/`Draft` heading, no explanation, no translation, and no next-step guidance]
{EXPLANATION_MARKER}
[one to three short sentences in {reply_language}; state that the draft was not sent and may be reviewed before the user sends it]
{END_MARKER}
The markers are routing delimiters, not user-visible content. Never merge the explanation into the draft block. Never omit or rename a marker.
""".strip()


def default_draft_explanation(language: str) -> str:
    messages = {
        "ar": (
            "المسودة في الرسالة السابقة منفصلة وجاهزة للنسخ. لم يتم إرسالها؛ "
            "راجع البيانات ثم أرسلها بنفسك إذا كانت مناسبة."
        ),
        "de": (
            "Der Entwurf steht in der vorherigen Nachricht separat und kann direkt "
            "kopiert werden. Er wurde nicht versendet; prüfe ihn vor dem eigenen Versand."
        ),
        "en": (
            "The draft is in the previous message by itself and can be copied directly. "
            "It was not sent; review it before sending it yourself."
        ),
        "uk": (
            "Чернетка міститься окремо в попередньому повідомленні, тому її можна "
            "скопіювати. Її не надіслано; перевір її перед самостійним надсиланням."
        ),
        "el": (
            "Το προσχέδιο βρίσκεται μόνο του στο προηγούμενο μήνυμα και μπορεί να "
            "αντιγραφεί. Δεν έχει σταλεί· έλεγξέ το πριν το στείλεις εσύ."
        ),
    }
    return messages.get(language, messages["de"])


def _strip_outer_code_fence(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text.strip()


def _strip_meta_heading(text: str) -> str:
    lines = _strip_outer_code_fence(text).splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ""

    first = lines[0].strip().strip("*_# ")
    if _META_HEADING.match(first):
        blank_index = next(
            (index for index, line in enumerate(lines[1:5], start=1) if not line.strip()),
            None,
        )
        lines = lines[blank_index + 1:] if blank_index is not None else lines[1:]
    return "\n".join(lines).strip()


def _strip_first_explanation_heading(text: str) -> str:
    value = text.strip()
    match = _EXPLANATION_HEADING.match(value)
    return value[match.end():].lstrip() if match else value


def _looks_like_official_draft(text: str) -> bool:
    lowered = text.casefold()
    salutations = tuple(marker.casefold() for marker in (
        "sehr geehrte", "guten tag", "dear ", "to whom it may concern",
        "السادة", "السيد", "السيدة", "шановн", "αξιότιμ",
    ))
    closings = tuple(marker.casefold() for marker in (
        "mit freundlichen Grüßen", "mit freundlichen Gruessen", "freundliche Grüße",
        "sincerely", "kind regards", "مع خالص التحية", "مع الاحترام", "з повагою", "με εκτίμηση",
    ))
    return (
        any(marker in lowered for marker in salutations)
        and any(marker in lowered for marker in closings)
    ) or (
        "betreff:" in lowered
        and any(marker in lowered for marker in closings)
    )


def draft_reply_requires_fail_closed(value: str) -> bool:
    """Identify malformed marker or draft-like output that must never be sent mixed."""
    text = _strip_outer_code_fence(_normalize_text(value))
    if not text:
        return False
    if any(marker.casefold() in text.casefold() for marker in (
        DRAFT_MARKER,
        EXPLANATION_MARKER,
        END_MARKER,
    )):
        return True
    cleaned = _strip_meta_heading(text)
    return bool(_EXPLANATION_HEADING.search(text) or _looks_like_official_draft(cleaned))


def _marker_split(text: str) -> tuple[str, str] | None:
    pattern = re.compile(
        rf"^\s*{re.escape(DRAFT_MARKER)}\s*(.*?)\s*"
        rf"{re.escape(EXPLANATION_MARKER)}\s*(.*?)\s*"
        rf"{re.escape(END_MARKER)}\s*$",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.match(text)
    if not match:
        return None
    return match.group(1), match.group(2)


def _legacy_split(text: str) -> tuple[str, str] | None:
    for match in _EXPLANATION_HEADING.finditer(text):
        if match.start() < 60:
            continue
        draft = text[:match.start()].strip()
        explanation = text[match.end():].strip()
        if draft and explanation:
            return draft, explanation
    return None


def parse_copy_safe_draft_reply(
    value: str,
    *,
    conversation_language: str,
) -> CopySafeDraftReply | None:
    """Validate and split one official-draft model reply, failing closed on ambiguity."""
    text = _strip_outer_code_fence(_normalize_text(value))
    if not text:
        return None

    split = _marker_split(text)
    if split is None:
        marker_count = sum(marker.casefold() in text.casefold() for marker in (
            DRAFT_MARKER,
            EXPLANATION_MARKER,
            END_MARKER,
        ))
        if marker_count:
            return None
        split = _legacy_split(text)

    language = conversation_language if conversation_language in _SUPPORTED_LANGUAGES else "de"
    if split is None:
        draft = _strip_meta_heading(text)
        if not _looks_like_official_draft(draft):
            return None
        explanation = default_draft_explanation(language)
    else:
        raw_draft, raw_explanation = split
        draft = _strip_meta_heading(raw_draft)
        explanation = _strip_first_explanation_heading(
            _strip_outer_code_fence(raw_explanation)
        )
        if not explanation:
            explanation = default_draft_explanation(language)

    if len(draft) < 20:
        return None
    if _EXPLANATION_HEADING.search(draft):
        return None
    if any(marker in draft or marker in explanation for marker in (
        DRAFT_MARKER,
        EXPLANATION_MARKER,
        END_MARKER,
    )):
        return None

    return CopySafeDraftReply(
        draft=draft.strip(),
        explanation=explanation.strip(),
        conversation_language=language,
    )
