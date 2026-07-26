"""Anonymous quality feedback for controlled AmtHero24 beta launches."""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}
_ALLOWED_TOPICS = {
    "document", "invoice", "jobcenter", "residence", "housing", "work", "language",
    "human_support", "reminder", "mission", "general", "unknown",
}

_POSITIVE = {
    "👍", "مفيد", "ممتاز", "ساعدني", "الجواب مفيد", "gut", "hilfreich", "sehr gut",
    "helpful", "good answer", "дякую допомогло", "корисно", "βοηθητικο", "χρησιμο",
}
_NEGATIVE = {
    "👎", "مو مفيد", "مش مفيد", "ما ساعدني", "الجواب غلط", "nicht hilfreich", "schlecht",
    "not helpful", "bad answer", "не допомогло", "не корисно", "δεν βοηθησε", "οχι χρησιμο",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def detect_feedback(text: str) -> int | None:
    """Return a 1-5 score only for explicit, compact feedback messages."""
    raw = (text or "").strip()
    normalized = _normalize(raw)
    if not normalized or len(raw) > 80:
        return None
    if raw in {"👍", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿"} or normalized in {_normalize(v) for v in _POSITIVE}:
        return 5
    if raw in {"👎", "👎🏻", "👎🏼", "👎🏽", "👎🏾", "👎🏿"} or normalized in {_normalize(v) for v in _NEGATIVE}:
        return 1
    match = re.fullmatch(r"(?:تقييمي|bewertung|rating|оцінка|βαθμολογια)?\s*([1-5])(?:\s*(?:من|von|out of|з|απο)\s*5)?", normalized)
    return int(match.group(1)) if match else None


def feedback_ack(language: str, score: int) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    positive = score >= 4
    messages = {
        "ar": ("شكراً 🙌 سجّلت تقييمك بشكل مجهول." if positive else "شكراً لصراحتك. سجّلت التقييم بشكل مجهول حتى نحسّن سام."),
        "de": ("Danke 🙌 Dein Feedback wurde anonym gespeichert." if positive else "Danke für die ehrliche Rückmeldung. Sie wurde anonym gespeichert, damit Sam besser wird."),
        "en": ("Thanks 🙌 Your feedback was stored anonymously." if positive else "Thanks for being honest. The anonymous rating will help improve Sam."),
        "uk": ("Дякую 🙌 Відгук збережено анонімно." if positive else "Дякую за чесність. Анонімна оцінка допоможе покращити Sam."),
        "el": ("Ευχαριστώ 🙌 Η αξιολόγηση αποθηκεύτηκε ανώνυμα." if positive else "Ευχαριστώ για την ειλικρίνεια. Η ανώνυμη αξιολόγηση θα βοηθήσει να βελτιωθεί ο Sam."),
    }
    return messages[lang]


def _safe_topic(topic: Any) -> str:
    normalized = _normalize(str(topic or "unknown")).replace(" ", "_")[:40]
    return normalized if normalized in _ALLOWED_TOPICS else "general"


class FeedbackRepository:
    """Store content-free quality events in PostgreSQL or the JSON fallback."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS anonymous_feedback (
                        feedback_id TEXT PRIMARY KEY,
                        score SMALLINT NOT NULL CHECK (score BETWEEN 1 AND 5),
                        language TEXT NOT NULL DEFAULT 'de',
                        topic TEXT NOT NULL DEFAULT 'general',
                        source TEXT NOT NULL DEFAULT 'whatsapp',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS anonymous_feedback_created_idx ON anonymous_feedback (created_at DESC)")

    def record(self, score: int, *, language: str, topic: str, source: str = "whatsapp") -> dict[str, Any]:
        safe_score = max(1, min(int(score), 5))
        event = {
            "feedback_id": uuid4().hex,
            "score": safe_score,
            "language": language if language in SUPPORTED_LANGUAGES else "de",
            "topic": _safe_topic(topic),
            "source": "whatsapp" if source != "admin" else "admin",
            "created_at": _now().isoformat(),
        }
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO anonymous_feedback (feedback_id, score, language, topic, source) VALUES (%s, %s, %s, %s, %s)",
                    (event["feedback_id"], event["score"], event["language"], event["topic"], event["source"]),
                )
            return event

        def add(data: dict[str, Any]) -> dict[str, Any]:
            records = data.setdefault("anonymous_feedback", [])
            records.append(deepcopy(event))
            data["anonymous_feedback"] = records[-10000:]
            return deepcopy(event)

        return self.store._transaction(add)

    def aggregate(self, *, days: int = 30) -> dict[str, Any]:
        cutoff = _now() - timedelta(days=max(1, min(int(days), 365)))
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as conn:
                rows = conn.execute(
                    "SELECT score, language, topic FROM anonymous_feedback WHERE created_at >= %s",
                    (cutoff,),
                ).fetchall()
            records = [dict(row) for row in rows]
        else:
            records = []
            for item in self.store.snapshot().get("anonymous_feedback", []):
                try:
                    created = datetime.fromisoformat(str(item.get("created_at")))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=UTC)
                except (TypeError, ValueError):
                    continue
                if created >= cutoff:
                    records.append(dict(item))

        scores = [int(item["score"]) for item in records]
        distribution = {str(score): scores.count(score) for score in range(1, 6)}
        topics: dict[str, int] = {}
        languages: dict[str, int] = {}
        for item in records:
            topics[str(item.get("topic") or "general")] = topics.get(str(item.get("topic") or "general"), 0) + 1
            languages[str(item.get("language") or "de")] = languages.get(str(item.get("language") or "de"), 0) + 1
        return {
            "period_days": max(1, min(int(days), 365)),
            "responses": len(scores),
            "average_score": round(sum(scores) / len(scores), 2) if scores else None,
            "positive_rate_percent": round(100 * sum(1 for score in scores if score >= 4) / len(scores), 1) if scores else None,
            "score_distribution": distribution,
            "topics": topics,
            "languages": languages,
        }

    def cleanup(self, *, days: int = 365) -> int:
        cutoff = _now() - timedelta(days=max(30, int(days)))
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as conn:
                cursor = conn.execute("DELETE FROM anonymous_feedback WHERE created_at < %s", (cutoff,))
                return max(cursor.rowcount, 0)

        def clean(data: dict[str, Any]) -> int:
            before = list(data.get("anonymous_feedback", []))
            kept = []
            for item in before:
                try:
                    created = datetime.fromisoformat(str(item.get("created_at")))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=UTC)
                except (TypeError, ValueError):
                    continue
                if created >= cutoff:
                    kept.append(item)
            data["anonymous_feedback"] = kept
            return len(before) - len(kept)

        return self.store._transaction(clean)
