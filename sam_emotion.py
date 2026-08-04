"""Bounded emotional-tone guidance for Sam.

This module reacts only to explicit conversational signals in the current turn.
It does not diagnose mental state, persist emotional profiles, or change product behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SamEmotionState:
    signal: str
    confidence: str


_CONFUSION = (
    "confused", "i don't understand", "i dont understand", "unclear", "verstehe nicht",
    "unverständlich", "مش فاهم", "مو فاهم", "ما فهمت", "مش واضح", "незрозуміло",
    "не розумію", "δεν καταλαβαίνω", "δεν είναι σαφές",
)
_FEAR = (
    "afraid", "scared", "worried", "panic", "angst", "sorge", "panik",
    "خايف", "خائف", "قلقان", "مرعوب", "боюся", "хвилююся", "страшно",
    "φοβάμαι", "αγχώνομαι",
)
_FRUSTRATION = (
    "frustrated", "fed up", "annoyed", "wütend", "genervt", "sauer",
    "معصب", "غاضب", "مقهور", "زهقت", "злий", "роздратований",
    "θυμωμένος", "εκνευρισμένος",
)
_RELIEF = (
    "relieved", "that helps", "thank god", "erleichtert", "das hilft",
    "ارتحت", "هلق فهمت", "الحمد لله", "полегшало", "це допомогло",
    "ανακουφίστηκα", "με βοήθησε",
)
_POSITIVE = (
    "great", "perfect", "excellent", "super", "perfekt", "ممتاز", "رائع",
    "تمام", "чудово", "супер", "τέλεια", "υπέροχα",
)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def _contains(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = _normalized(text)
    return any(phrase.casefold() in normalized for phrase in phrases)


def infer_emotion_state(text: str) -> SamEmotionState:
    """Infer only explicit current-turn signals, with a safe neutral fallback."""
    if _contains(text, _FRUSTRATION):
        return SamEmotionState("frustrated", "explicit")
    if _contains(text, _FEAR):
        return SamEmotionState("worried", "explicit")
    if _contains(text, _CONFUSION):
        return SamEmotionState("confused", "explicit")
    if _contains(text, _RELIEF):
        return SamEmotionState("relieved", "explicit")
    if _contains(text, _POSITIVE):
        return SamEmotionState("positive", "explicit")
    return SamEmotionState("neutral", "none")


def build_sam_emotion_contract(*, text: str) -> str:
    """Return bounded tone guidance for the current reply."""
    state = infer_emotion_state(text)
    guidance = {
        "frustrated": (
            "Acknowledge the practical frustration in one short sentence. Do not defend, moralize, or mirror anger. "
            "Move quickly to the fix or the next concrete action."
        ),
        "worried": (
            "Reduce uncertainty with one grounded reassurance. Distinguish known facts from unknowns and never promise an outcome."
        ),
        "confused": (
            "Slow down, remove jargon, explain the core point in plain language, and give one example only if it improves clarity."
        ),
        "relieved": (
            "Recognize the progress briefly and keep momentum without over-celebrating or becoming sentimental."
        ),
        "positive": (
            "Match the positive tone lightly, credit the user's progress, and continue with the next useful step."
        ),
        "neutral": (
            "Use a calm, direct, helpful tone. Do not invent feelings or add emotional language without evidence."
        ),
    }[state.signal]
    return f"""
SAM EMOTIONAL TONE
- Current explicit signal: {state.signal}.
- Signal confidence: {state.confidence}.
- {guidance}
- Do not diagnose, label, or store an emotional profile.
- Do not use empathy as persuasion, marketing pressure, or a dependency mechanism.
- Keep emotional acknowledgement shorter than the practical help.
""".strip()
