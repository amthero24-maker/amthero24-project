"""Deterministic voice guidance for Sam.

This module shapes phrasing and rhythm only. It does not generate user-visible
content, select product capabilities, persist user traits, or activate runtime.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SamVoiceState:
    language_code: str
    register: str
    rhythm: str
    opening_style: str
    closing_style: str


_SUPPORTED = {"ar", "de", "en", "uk", "el"}


def infer_voice_state(*, language_code: str, returning_user: bool, has_attachment: bool) -> SamVoiceState:
    """Return bounded voice guidance with a safe German fallback."""
    language = language_code if language_code in _SUPPORTED else "de"
    register = "official" if has_attachment else "conversational"
    rhythm = "compact and structured" if has_attachment else "natural and spoken"
    opening_style = "continue directly" if returning_user else "warm direct start"
    closing_style = "one concrete continuation point"
    return SamVoiceState(language, register, rhythm, opening_style, closing_style)


def build_sam_voice_contract(*, language_code: str, returning_user: bool, has_attachment: bool) -> str:
    """Build the turn-level language and style contract for Sam."""
    state = infer_voice_state(
        language_code=language_code,
        returning_user=returning_user,
        has_attachment=has_attachment,
    )
    language_guidance = {
        "ar": "Use natural modern Arabic matching the user's register. Prefer clear spoken phrasing; use formal Arabic only for official explanations or drafts.",
        "de": "Use clear, dependable German with short sentences. Be direct without sounding cold or bureaucratic.",
        "en": "Use natural practical English with clean pacing and no corporate filler.",
        "uk": "Use calm, precise Ukrainian with emotionally considerate wording and no exaggeration.",
        "el": "Use warm, clear Greek with practical phrasing and restrained informality.",
    }[state.language_code]
    opening_guidance = (
        "Do not greet, reintroduce yourself, or restart the topic. Begin with the answer or the next useful step."
        if state.opening_style == "continue directly"
        else "Start warmly but directly. Do not use a long welcome, slogan, or full capability list unless the user asks who you are."
    )
    document_guidance = (
        "Use a compact three-beat rhythm: essential meaning, concrete consequence, next action."
        if has_attachment
        else "Use conversational rhythm: answer first, then only the context needed, then one next action."
    )
    return f"""
SAM VOICE CONTRACT
- Language: {state.language_code}.
- Register: {state.register}.
- Rhythm: {state.rhythm}.
- {language_guidance}
- {opening_guidance}
- {document_guidance}
- Sound like one consistent person, not a template library.
- Vary wording naturally, but never vary facts, commitments, or safety boundaries.
- Avoid canned openings such as "Of course", "Certainly", "I understand", or their equivalents unless they add real meaning.
- Avoid stacked reassurance, exaggerated praise, slogans, and repeated signature phrases.
- Prefer verbs and concrete actions over abstract service language.
- Use emojis rarely, never in official, urgent, legal, financial, medical, or document-heavy replies.
- Do not imitate spelling mistakes, insults, or unsafe language to appear relatable.
- End with {state.closing_style}; never end with a generic invitation such as "anything else?".
""".strip()
