"""Deterministic conversation-stage guidance for Sam.

This module coordinates the conversational flow only. It does not execute
missions, persist state, infer hidden emotions, or activate runtime features.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SamConversationState:
    stage: str
    mission_status: str
    continuation: str


_GREETING_ONLY = (
    "hi", "hello", "hey", "hallo", "guten tag", "مرحبا", "مرحباً", "اهلا", "أهلا",
    "привіт", "добрий день", "γεια", "καλημέρα",
)
_ACTIVE_MISSION_STATUSES = {
    "started", "understood", "document_created", "sent", "follow_up", "escalation",
    "active", "in_progress",
}
_AWAITING_STATUSES = {"awaiting_reply", "waiting", "pending_reply"}
_FINISHED_STATUSES = {"mission_finished", "finished", "completed", "done", "closed"}
_TRANSIENT_CONVERSATION_TOPICS = {"identity", "capabilities", "greeting"}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", (value or "").casefold()).strip("_")


def _is_greeting_only(text: str) -> bool:
    normalized = re.sub(r"[\s.!?,،؛]+", " ", (text or "").casefold()).strip()
    return normalized in {item.casefold() for item in _GREETING_ONLY}


def _is_short_context_answer(text: str) -> bool:
    """Return true for a compact answer that likely responds to Sam's last question.

    This is deliberately structural rather than semantic: it does not guess the
    user's intent or execute anything. It only tells the prompt layer to preserve
    the immediately preceding conversational question instead of treating a
    1-4-word answer as a brand-new standalone FAQ query.
    """
    cleaned = re.sub(r"[.!?,،؛؟]+", " ", (text or "").strip())
    words = [item for item in cleaned.split() if item]
    return bool(words) and len(words) <= 4 and len(cleaned) <= 60


def infer_conversation_state(
    *,
    text: str,
    returning_user: bool,
    has_attachment: bool,
    current_topic: str,
    mission_status: str,
) -> SamConversationState:
    """Infer the safest next conversational stage from explicit context only."""
    normalized_status = _normalize(mission_status)
    topic = (current_topic or "").strip()
    known_topic = bool(topic) and topic.casefold() != "unknown"
    transient_topic = topic.casefold() in _TRANSIENT_CONVERSATION_TOPICS

    if normalized_status in _FINISHED_STATUSES:
        stage = "mission_finished"
        continuation = "close the completed mission clearly, then preserve continuity without inventing a new task"
    elif normalized_status in _AWAITING_STATUSES:
        stage = "follow_up"
        continuation = "state what is being awaited and the next checkpoint without pretending a reply has arrived"
    elif normalized_status in _ACTIVE_MISSION_STATUSES:
        stage = "mission"
        continuation = "advance the existing mission by one verified step and do not restart discovery"
    elif has_attachment:
        stage = "organize"
        continuation = "extract the essential facts, organize the task, and identify one safe next action"
    elif not returning_user and _is_greeting_only(text):
        stage = "greeting"
        continuation = "welcome briefly and invite the smallest useful description of the task"
    elif returning_user and not known_topic and _is_greeting_only(text):
        stage = "relationship_continues"
        continuation = "acknowledge the return briefly and ask what should be continued or started"
    elif returning_user and transient_topic and _is_short_context_answer(text):
        stage = "contextual_followup"
        continuation = "treat the short message as a likely answer to Sam's immediately preceding question; acknowledge it naturally and ask one useful next question instead of giving a generic definition or restarting discovery"
    else:
        stage = "understand"
        continuation = "confirm the real objective and only the missing fact needed for the next action"

    return SamConversationState(
        stage=stage,
        mission_status=normalized_status or "none",
        continuation=continuation,
    )


def build_sam_conversation_contract(
    *,
    text: str,
    returning_user: bool,
    has_attachment: bool,
    current_topic: str,
    mission_status: str,
) -> str:
    """Build bounded stage guidance for the current turn."""
    state = infer_conversation_state(
        text=text,
        returning_user=returning_user,
        has_attachment=has_attachment,
        current_topic=current_topic,
        mission_status=mission_status,
    )
    stage_guidance = {
        "greeting": "Do not list all capabilities. Establish trust quickly and ask for the user's immediate administrative goal.",
        "understand": "Answer what is already clear, then ask at most one necessary clarifying question. Do not interrogate the user.",
        "organize": "Turn the available facts into a simple structure: objective, relevant facts, risk or deadline if explicit, next action.",
        "mission": "Treat the conversation as an active mission. Preserve completed work, name the current step, and move it forward safely.",
        "follow_up": "Maintain the waiting state accurately. Distinguish between no reply yet, a new reply, and a deadline requiring action.",
        "mission_finished": "Mark the result and any remaining obligation precisely. Do not imply ongoing work when the mission is complete.",
        "relationship_continues": "Keep the relationship continuous without manufacturing familiarity, dependency, or an unfinished mission.",
        "contextual_followup": "Do not answer the short phrase as a dictionary or FAQ lookup. Use the previous assistant question and recent messages to understand what the user is answering. Respond conversationally in 1-3 short sentences and ask at most one concrete next question.",
    }[state.stage]
    return f"""
SAM CONVERSATION CONTRACT
- Current stage: {state.stage}.
- Explicit mission status: {state.mission_status}.
- {stage_guidance}
- This turn should {state.continuation}.
- Follow the lifecycle when supported by facts: Greeting -> Understand -> Organize -> Mission -> Follow-up -> Mission Finished -> Relationship continues.
- Never skip directly to execution when required information, consent, authorization, or a runtime gate is missing.
- Never claim a document was sent, an appointment was booked, a reply was received, or a mission was completed unless verified context says so.
- Keep one primary objective per reply. Do not open unrelated missions.
""".strip()
