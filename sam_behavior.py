"""Deterministic behavior guidance for Sam.

The engine does not diagnose emotions or make product decisions. It provides
bounded conversation guidance from the current text and known continuity.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SamBehaviorState:
    mode: str
    urgency: str
    emotional_signal: str
    detail_preference: str


_URGENT = (
    "urgent", "asap", "today", "immediately", "frist", "heute", "sofort",
    "عاجل", "اليوم", "فورا", "فوراً", "терміново", "сьогодні", "επείγον", "σήμερα",
)
_STRESSED = (
    "afraid", "scared", "worried", "panic", "stress", "angst", "sorge", "panik",
    "خايف", "خائف", "قلقان", "متوتر", "مرعوب", "боюся", "хвилююся", "страшно",
    "φοβάμαι", "αγχώνομαι",
)
_ANGRY = (
    "angry", "furious", "wütend", "sauer", "غاضب", "معصب", "مقهور",
    "злий", "розлючений", "θυμωμένος", "έξαλλος",
)
_DETAIL = (
    "explain in detail", "details", "ausführlich", "genau erklären", "بالتفصيل",
    "اشرحلي بالتفصيل", "детально", "поясни детально", "αναλυτικά",
)
_BRIEF = (
    "briefly", "short answer", "kurz", "kurz gesagt", "باختصار", "مختصر",
    "коротко", "σύντομα",
)
_IDENTITY = (
    "who are you", "who made you", "who do you belong to", "wer bist du",
    "wer hat dich gemacht", "wem gehörst du", "من انت", "من أنت", "مين صنعك",
    "لمن تتبع", "хто ти", "хто тебе створив", "ποιος είσαι", "ποιος σε έφτιαξε",
)


def _contains(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").casefold()).strip()
    return any(phrase.casefold() in normalized for phrase in phrases)


def infer_behavior_state(*, text: str, returning_user: bool, has_attachment: bool) -> SamBehaviorState:
    """Infer bounded conversational guidance without storing personal data."""
    if _contains(text, _IDENTITY):
        mode = "identity"
    elif has_attachment:
        mode = "document"
    elif returning_user:
        mode = "continuation"
    else:
        mode = "first_contact"

    urgency = "high" if _contains(text, _URGENT) else "normal"
    if _contains(text, _ANGRY):
        emotional_signal = "frustrated"
    elif _contains(text, _STRESSED):
        emotional_signal = "stressed"
    else:
        emotional_signal = "neutral"

    if _contains(text, _DETAIL):
        detail_preference = "detailed"
    elif _contains(text, _BRIEF):
        detail_preference = "brief"
    else:
        detail_preference = "adaptive"

    return SamBehaviorState(mode, urgency, emotional_signal, detail_preference)


def build_sam_behavior_contract(*, text: str, returning_user: bool, has_attachment: bool) -> str:
    """Return explicit response behavior for the current turn."""
    state = infer_behavior_state(
        text=text,
        returning_user=returning_user,
        has_attachment=has_attachment,
    )
    mode_guidance = {
        "identity": "Answer the identity or company question confidently and accurately; do not turn it into a sales pitch.",
        "document": "Lead with the document's essential meaning, then the risk or deadline only if visible, then one next action.",
        "continuation": "Continue from the existing context without greeting, reintroduction, or repeating completed explanations.",
        "first_contact": "Create immediate clarity and trust, then invite the smallest useful first action without listing everything at once.",
    }[state.mode]
    emotion_guidance = {
        "frustrated": "Do not defend, debate, or mirror anger. Acknowledge the practical frustration briefly and move to resolution.",
        "stressed": "Reduce uncertainty first with one grounded reassurance; never guarantee an outcome that is not known.",
        "neutral": "Do not invent emotion or add unnecessary reassurance.",
    }[state.emotional_signal]
    detail_guidance = {
        "detailed": "Give a structured explanation with enough detail to support a decision, while keeping a clear next step.",
        "brief": "Answer in the fewest complete sentences that preserve accuracy and a concrete next step.",
        "adaptive": "Start concise and expand only where the task, risk, or user question requires it.",
    }[state.detail_preference]
    urgency_guidance = (
        "Treat time as important: surface any known deadline and give the fastest safe next step. Do not create artificial urgency."
        if state.urgency == "high"
        else "Use normal pacing and do not introduce urgency that the user did not express."
    )
    return f"""
SAM TURN BEHAVIOR
- Conversation mode: {state.mode}.
- Urgency: {state.urgency}.
- Emotional signal: {state.emotional_signal}.
- Detail preference: {state.detail_preference}.
- {mode_guidance}
- {emotion_guidance}
- {detail_guidance}
- {urgency_guidance}
- Every reply must do useful work: answer, clarify, or advance the mission. Avoid filler.
- End with one context-specific continuation point, not a generic offer for more help.
""".strip()
